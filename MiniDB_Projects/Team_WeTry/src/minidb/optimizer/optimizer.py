"""Cost-based optimizer: turns a parsed SELECT into a physical operator tree.

Choices it makes, recorded in PlanInfo.notes for EXPLAIN:
* Access path -- IndexScan when an equality predicate hits the primary key and
  its estimated cost (from row_count / ndistinct selectivity) beats a SeqScan.
* Join algorithm -- Index-Nested-Loop when the inner can be probed by its PK,
  else a plain Nested-Loop.
* Join order -- greedy left-deep, smallest estimated relation first.

Predicates are split on top-level AND; single-table conjuncts are pushed to the
scan, multi-table ones become join predicates or a residual Filter.
"""

from ..sql import ast
from ..execution.operators import (
    SeqScan, IndexScan, Filter, NestedLoopJoin, IndexNestedLoopJoin)


class PlanInfo:
    def __init__(self, root, sources, notes):
        self.root = root          # operator tree
        self.sources = sources    # [(alias, table_info)] in textual order
        self.notes = notes        # list[str] for EXPLAIN


# ---- predicate utilities ------------------------------------------------
def split_and(node):
    if node is None:
        return []
    if isinstance(node, ast.BinOp) and node.op == "AND":
        return split_and(node.left) + split_and(node.right)
    return [node]


def _column_alias(ref, col_to_alias):
    if ref.table is not None:
        return ref.table
    return col_to_alias.get(ref.name)


def aliases_in(node, col_to_alias):
    """Set of table aliases referenced by an expression."""
    out = set()
    if isinstance(node, ast.ColumnRef):
        a = _column_alias(node, col_to_alias)
        if a:
            out.add(a)
    elif isinstance(node, ast.BinOp):
        out |= aliases_in(node.left, col_to_alias)
        out |= aliases_in(node.right, col_to_alias)
    return out


def eq_col_literal(conj, alias, ti, col_to_alias):
    """If conj is `<alias.col> = <literal>`, return (col, value), else None."""
    if not (isinstance(conj, ast.BinOp) and conj.op == "="):
        return None
    for a, b in ((conj.left, conj.right), (conj.right, conj.left)):
        if isinstance(a, ast.ColumnRef) and isinstance(b, ast.Literal):
            if _column_alias(a, col_to_alias) == alias and a.name in ti.schema.names:
                return a.name, b.value
    return None


def eq_join_on_pk(pred, inner_alias, inner_ti, joined_aliases, col_to_alias):
    """If pred equates inner.pk to an expression over already-joined tables,
    return that expression (so we can probe the inner index). Else None."""
    if not (isinstance(pred, ast.BinOp) and pred.op == "="):
        return None
    pk = inner_ti.pk_column
    if pk is None:
        return None
    for a, b in ((pred.left, pred.right), (pred.right, pred.left)):
        if (isinstance(a, ast.ColumnRef)
                and _column_alias(a, col_to_alias) == inner_alias
                and a.name == pk):
            other = aliases_in(b, col_to_alias)
            if other and other <= joined_aliases:
                return b
    return None


# ---- selectivity / cost -------------------------------------------------
def _selectivity(conj, alias, ti, stats, col_to_alias):
    eq = eq_col_literal(conj, alias, ti, col_to_alias)
    if eq is not None:
        col, _ = eq
        nd = stats["ndistinct"].get(col)
        return 1.0 / nd if nd else 0.1
    return 0.3   # default for ranges / other comparisons


# ---- planning -----------------------------------------------------------
def plan_select(engine, ctx, stmt):
    notes = []

    # Build the list of sources in textual order.
    sources = []   # (alias, table_info)
    from_alias = stmt.from_alias or stmt.from_table
    sources.append((from_alias, engine.catalog.get_table(stmt.from_table)))
    for jc in stmt.joins:
        jalias = jc.alias or jc.table
        sources.append((jalias, engine.catalog.get_table(jc.table)))

    # Column -> alias map for resolving bare column references.
    col_to_alias = {}
    for alias, ti in sources:
        for cn in ti.schema.names:
            col_to_alias.setdefault(cn, alias)

    conjuncts = split_and(stmt.where)

    # Assign single-table conjuncts to their table; keep the rest as residual.
    by_alias = {alias: [] for alias, _ in sources}
    residual = []
    for c in conjuncts:
        al = aliases_in(c, col_to_alias)
        if len(al) == 1:
            by_alias[next(iter(al))].append(c)
        else:
            residual.append(c)

    # Per-source planning metadata.
    info = {}
    for alias, ti in sources:
        stats = engine.table_stats(ti.name)
        pushed = by_alias[alias]
        est = stats["row_count"]
        for c in pushed:
            est *= _selectivity(c, alias, ti, stats, col_to_alias)
        info[alias] = {
            "alias": alias, "ti": ti, "stats": stats,
            "pushed": pushed, "est": max(1.0, est),
        }

    def make_scan(alias):
        """Fresh access operator for a source (driving-table access path)."""
        meta = info[alias]
        ti = meta["ti"]
        heap = engine.heaps[ti.name]
        pushed = meta["pushed"]
        # Try an index access path on the primary key.
        pk = ti.pk_column
        idx = engine.indexes.get(ti.name, {}).get(pk) if pk else None
        chosen_eq = None
        if idx is not None:
            for c in pushed:
                eq = eq_col_literal(c, alias, ti, col_to_alias)
                if eq is not None and eq[0] == pk:
                    chosen_eq = (c, eq[1])
                    break
        R = meta["stats"]["row_count"]
        if chosen_eq is not None:
            sel = 1.0 / max(1, meta["stats"]["ndistinct"].get(pk, R))
            idx_cost = R * sel + idx.height()
            seq_cost = max(1, R)
            if idx_cost <= seq_cost:
                notes.append(
                    f"{ti.name}: IndexScan on {pk} "
                    f"(cost~{idx_cost:.1f} < seqscan~{seq_cost})")
                op = IndexScan(ctx, alias, ti, heap, idx, pk, key=chosen_eq[1])
                rest = [c for c in pushed if c is not chosen_eq[0]]
                return Filter(op, _and(rest)) if rest else op
            notes.append(
                f"{ti.name}: SeqScan (index cost~{idx_cost:.1f} "
                f">= seqscan~{seq_cost})")
        else:
            notes.append(f"{ti.name}: SeqScan (no usable index predicate)")
        op = SeqScan(ctx, alias, ti, heap)
        return Filter(op, _and(pushed)) if pushed else op

    # Single table: just the scan.
    if not stmt.joins:
        root = make_scan(from_alias)
        if residual:
            root = Filter(root, _and(residual))
        return PlanInfo(root, sources, notes)

    # ---- greedy left-deep join ordering -------------------------------
    on_preds = [jc.on for jc in stmt.joins]
    pending_join = list(on_preds)        # ON predicates not yet consumed
    pending_residual = list(residual)

    remaining = [alias for alias, _ in sources]
    # Start from the smallest estimated relation.
    remaining.sort(key=lambda a: info[a]["est"])
    first = remaining.pop(0)
    notes.append(f"join order: start with {info[first]['ti'].name} "
                 f"(est~{info[first]['est']:.0f} rows)")
    root = make_scan(first)
    joined = {first}

    while remaining:
        # Choose the next inner: prefer one connected by a usable predicate,
        # breaking ties by smallest estimated size.
        remaining.sort(key=lambda a: info[a]["est"])
        nxt = remaining.pop(0)
        meta = info[nxt]
        ti = meta["ti"]

        # Collect predicates now applicable (reference only joined ∪ {nxt}).
        applicable = []
        for src in (pending_join, pending_residual):
            for p in list(src):
                al = aliases_in(p, col_to_alias)
                if al <= (joined | {nxt}) and (nxt in al):
                    applicable.append(p)
                    src.remove(p)
        join_pred = _and(applicable)

        # Index-nested-loop if we can probe the inner's PK.
        probe_expr = None
        for p in applicable:
            probe_expr = eq_join_on_pk(p, nxt, ti, joined, col_to_alias)
            if probe_expr is not None:
                break
        idx = engine.indexes.get(ti.name, {}).get(ti.pk_column) if ti.pk_column else None

        if probe_expr is not None and idx is not None:
            notes.append(
                f"join {ti.name}: IndexNestedLoopJoin probing {ti.pk_column}")

            def build_probe(lrow, ti=ti, idx=idx, meta=meta, probe_expr=probe_expr):
                from ..execution.expr import eval_expr
                key = eval_expr(lrow, probe_expr)
                op = IndexScan(ctx, meta["alias"], ti, engine.heaps[ti.name],
                               idx, ti.pk_column, key=key)
                return Filter(op, _and(meta["pushed"])) if meta["pushed"] else op

            root = IndexNestedLoopJoin(root, build_probe, join_pred,
                                       right_label=ti.name)
        else:
            notes.append(f"join {ti.name}: NestedLoopJoin")
            inner_alias = nxt
            root = NestedLoopJoin(root,
                                  lambda inner_alias=inner_alias: make_scan(inner_alias),
                                  join_pred, right_label=ti.name)
        joined.add(nxt)

    leftover = pending_join + pending_residual
    if leftover:
        root = Filter(root, _and(leftover))
    return PlanInfo(root, sources, notes)


def _and(conjuncts):
    """Re-combine a list of conjuncts into a single AND expression (or None)."""
    if not conjuncts:
        return None
    expr = conjuncts[0]
    for c in conjuncts[1:]:
        expr = ast.BinOp("AND", expr, c)
    return expr
