"""Physical query operators (volcano / iterator model).

Each operator is iterable and yields row dicts keyed by "alias.column", pulling
from its children on demand so a plan runs as a pipeline. These cover the access
paths and joins the optimizer chooses between; projection, aggregation, sorting
and limiting are a post-pass in the engine. Reads go through ExecContext, which
applies snapshot visibility (MVCC) or lock-and-re-read (2PL).
"""

from .expr import eval_predicate


class Operator:
    name = "Operator"

    def __iter__(self):
        raise NotImplementedError

    def explain(self, indent=0):
        pad = "  " * indent
        line = f"{pad}{self._explain_line()}"
        for child in self._children():
            line += "\n" + child.explain(indent + 1)
        return line

    def _explain_line(self):
        return self.name

    def _children(self):
        return []


class SeqScan(Operator):
    name = "SeqScan"

    def __init__(self, ctx, alias, table_info, heap):
        self.ctx = ctx
        self.alias = alias
        self.table_info = table_info
        self.heap = heap
        self.rows_examined = 0

    def __iter__(self):
        schema = self.table_info.schema
        names = schema.names
        for rid, rec in self.heap.scan():
            self.rows_examined += 1
            visible, values = self.ctx.read_row(self.heap, rid, rec, schema)
            if not visible:
                continue
            yield {f"{self.alias}.{n}": v for n, v in zip(names, values)}

    def _explain_line(self):
        return f"SeqScan({self.table_info.name} AS {self.alias})"


class IndexScan(Operator):
    """Point / range lookup through a B+ tree index, then MVCC visibility."""
    name = "IndexScan"

    def __init__(self, ctx, alias, table_info, heap, index, column,
                 key=None, low=None, high=None):
        self.ctx = ctx
        self.alias = alias
        self.table_info = table_info
        self.heap = heap
        self.index = index
        self.column = column
        self.key = key          # equality lookup
        self.low = low          # range bounds (inclusive)
        self.high = high
        self.rows_examined = 0

    def _candidate_rids(self):
        if self.key is not None:
            return self.index.search(self.key)
        return [rid for _k, rid in self.index.range_scan(self.low, self.high)]

    def __iter__(self):
        schema = self.table_info.schema
        names = schema.names
        for rid in self._candidate_rids():
            rec = self.heap.get_record(rid)
            if rec is None:
                continue
            self.rows_examined += 1
            visible, values = self.ctx.read_row(self.heap, rid, rec, schema)
            if not visible:
                continue
            yield {f"{self.alias}.{n}": v for n, v in zip(names, values)}

    def _explain_line(self):
        if self.key is not None:
            pred = f"{self.column} = {self.key}"
        else:
            pred = f"{self.low} <= {self.column} <= {self.high}"
        return f"IndexScan({self.table_info.name} AS {self.alias}, {pred})"


class Filter(Operator):
    name = "Filter"

    def __init__(self, child, predicate):
        self.child = child
        self.predicate = predicate

    def __iter__(self):
        for row in self.child:
            if eval_predicate(row, self.predicate):
                yield row

    def _children(self):
        return [self.child]

    def _explain_line(self):
        return "Filter"


class NestedLoopJoin(Operator):
    """Classic nested-loop join. `right_factory` builds a fresh right iterator
    for each outer row (operators are single-pass generators)."""
    name = "NestedLoopJoin"

    def __init__(self, left, right_factory, predicate, right_label="?"):
        self.left = left
        self.right_factory = right_factory
        self.predicate = predicate
        self.right_label = right_label

    def __iter__(self):
        for lrow in self.left:
            for rrow in self.right_factory():
                merged = dict(lrow)
                merged.update(rrow)
                if eval_predicate(merged, self.predicate):
                    yield merged

    def _children(self):
        return [self.left]

    def _explain_line(self):
        return f"NestedLoopJoin(inner={self.right_label})"


class IndexNestedLoopJoin(Operator):
    """Nested-loop join where the inner side is probed via an index on its join
    key, turning an O(N*M) join into O(N*log M)."""
    name = "IndexNestedLoopJoin"

    def __init__(self, left, build_probe, predicate, right_label="?"):
        self.left = left
        self.build_probe = build_probe   # fn(left_row) -> iterable of right rows
        self.predicate = predicate
        self.right_label = right_label

    def __iter__(self):
        for lrow in self.left:
            for rrow in self.build_probe(lrow):
                merged = dict(lrow)
                merged.update(rrow)
                if eval_predicate(merged, self.predicate):
                    yield merged

    def _children(self):
        return [self.left]

    def _explain_line(self):
        return f"IndexNestedLoopJoin(inner={self.right_label})"
