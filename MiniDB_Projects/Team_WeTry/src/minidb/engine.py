"""MiniDB engine: the facade wiring every layer together.

It opens/closes the database (disk, WAL, buffer pool, catalog) and runs crash
recovery on startup, manages transactions and the active CC mode ("mvcc" |
"2pl"), and executes parsed SQL -- driving the optimizer + operator pipeline for
SELECT and applying versioned, WAL-logged mutations for INSERT/UPDATE/DELETE.

Mutations and their WAL appends run under a single write latch so the
append-log / apply-change / stamp-LSN sequence is atomic. Reads never take that
latch; under MVCC they run lock-free against a snapshot.
"""

import os
import threading

from .config import BUFFER_POOL_SIZE
from .storage.disk_manager import DiskManager
from .storage.buffer_pool import BufferPool
from .storage.table_heap import TableHeap
from .catalog.catalog import Catalog
from .record.schema import Schema, Column
from .record.tuple import (TupleMeta, pack_record, unpack_record,
                           record_with_meta, META_SIZE)
from .record.rid import RID
from .index.btree import BPlusTree
from .txn.transaction import TransactionManager
from .txn.lock_manager import LockManager, DeadlockError
from .recovery.wal import WriteAheadLog
from .recovery import recovery as recovery_mod
from .sql import ast
from .sql.parser import parse
from .execution.context import ExecContext
from .execution import expr as expr_mod
from . import optimizer as opt_mod


class MiniDBError(Exception):
    pass


class WriteConflictError(MiniDBError):
    pass


class Result:
    """Uniform statement result."""
    def __init__(self, columns=None, rows=None, message=None, rowcount=None):
        self.columns = columns
        self.rows = rows
        self.message = message
        self.rowcount = rowcount

    def __repr__(self):
        if self.columns is not None:
            return f"Result(columns={self.columns}, {len(self.rows)} rows)"
        return f"Result(message={self.message!r}, rowcount={self.rowcount})"


class Engine:
    def __init__(self, data_dir, mode="mvcc", buffer_pool_size=BUFFER_POOL_SIZE):
        if mode not in ("mvcc", "2pl"):
            raise ValueError("mode must be 'mvcc' or '2pl'")
        self.data_dir = data_dir
        self.mode = mode
        os.makedirs(data_dir, exist_ok=True)

        self.disk = DiskManager(os.path.join(data_dir, "minidb.data"))
        self.wal = WriteAheadLog(os.path.join(data_dir, "minidb.wal"))
        self.bp = BufferPool(self.disk, buffer_pool_size, wal_flush=self.wal.flush)
        self.catalog = Catalog(os.path.join(data_dir, "catalog.json"))

        self.txn_mgr = TransactionManager()
        self.lock_mgr = LockManager()
        self._write_latch = threading.Lock()

        self.heaps = {}        # table name -> TableHeap
        self.indexes = {}      # table name -> {column -> BPlusTree}
        self.row_counts = {}   # table name -> approx live row count (for stats)

        self._recover_and_build()

    # ---- startup --------------------------------------------------------
    def _recover_and_build(self):
        result = recovery_mod.recover(self.bp, self.wal)
        self.txn_mgr.restore(result.committed, result.max_txn_id + 1)
        for name, ti in self.catalog.tables.items():
            heap = TableHeap(self.bp, ti.table_id)
            self.heaps[name] = heap
            self.indexes[name] = {}
            count = 0
            if ti.pk_column:
                idx = BPlusTree()
                self.indexes[name][ti.pk_column] = idx
                pk_pos = ti.schema.index_of(ti.pk_column)
                for rid, rec in heap.scan():
                    _meta, values = unpack_record(rec, ti.schema)
                    idx.insert(values[pk_pos], rid)
                    count += 1
            else:
                count = sum(1 for _ in heap.scan())
            self.row_counts[name] = count

    def close(self):
        self.wal.log_checkpoint()
        self.bp.flush_all()
        self.catalog.save()
        self.wal.close()
        self.disk.close()

    # ---- transactions ---------------------------------------------------
    def begin_transaction(self):
        return self.txn_mgr.begin()

    def _ensure_begin_logged(self, txn):
        if not getattr(txn, "_begin_logged", False):
            self.wal.log_begin(txn.txn_id)
            txn._begin_logged = True

    def commit(self, txn):
        if getattr(txn, "_begin_logged", False):
            self.wal.log_commit(txn.txn_id)
        self.txn_mgr.commit(txn)
        if self.mode == "2pl":
            self.lock_mgr.release_all(txn.txn_id)

    def abort(self, txn):
        # Reverse this transaction's changes using its in-memory undo log.
        with self._write_latch:
            for kind, rid, before in reversed(txn.undo_log):
                heap = self._heap_by_table_id(rid.page_id)
                if kind == "insert":
                    page = self.bp.fetch_page(rid.page_id)
                    page.delete_tuple(rid.slot_id)
                    self.bp.unpin_page(rid.page_id, True)
                elif kind == "update":
                    page = self.bp.fetch_page(rid.page_id)
                    page.update_tuple(rid.slot_id, before)
                    self.bp.unpin_page(rid.page_id, True)
        if getattr(txn, "_begin_logged", False):
            self.wal.log_abort(txn.txn_id)
        self.txn_mgr.abort(txn)
        if self.mode == "2pl":
            self.lock_mgr.release_all(txn.txn_id)

    def _heap_by_table_id(self, page_id):
        page = self.bp.fetch_page(page_id)
        tid = page.table_id
        self.bp.unpin_page(page_id, False)
        for name, ti in self.catalog.tables.items():
            if ti.table_id == tid:
                return self.heaps[name]
        return None

    # ---- top-level execute ---------------------------------------------
    def execute(self, sql, txn=None):
        stmt = parse(sql)
        if isinstance(stmt, ast.Begin):
            return self.begin_transaction()
        if isinstance(stmt, ast.Commit):
            self.commit(txn)
            return Result(message="COMMIT")
        if isinstance(stmt, ast.Rollback):
            self.abort(txn)
            return Result(message="ROLLBACK")
        if isinstance(stmt, ast.CreateTable):
            return self._create_table(stmt)

        auto = txn is None
        if auto:
            txn = self.begin_transaction()
        try:
            if isinstance(stmt, ast.Insert):
                res = self._insert(stmt, txn)
            elif isinstance(stmt, ast.Select):
                res = self._select(stmt, txn)
            elif isinstance(stmt, ast.Delete):
                res = self._delete(stmt, txn)
            elif isinstance(stmt, ast.Update):
                res = self._update(stmt, txn)
            else:
                raise MiniDBError(f"unsupported statement {type(stmt).__name__}")
            if auto:
                self.commit(txn)
            return res
        except Exception:
            if auto:
                self.abort(txn)
            raise

    # ---- DDL ------------------------------------------------------------
    def _create_table(self, stmt):
        cols = [Column(n, t) for n, t in stmt.columns]
        schema = Schema(cols)
        ti = self.catalog.create_table(stmt.name, schema, stmt.pk_column)
        self.heaps[stmt.name] = TableHeap(self.bp, ti.table_id)
        self.indexes[stmt.name] = {}
        if ti.pk_column:
            self.indexes[stmt.name][ti.pk_column] = BPlusTree()
        self.row_counts[stmt.name] = 0
        return Result(message=f"CREATE TABLE {stmt.name}")

    # ---- INSERT ---------------------------------------------------------
    def _insert(self, stmt, txn):
        ti = self.catalog.get_table(stmt.table)
        schema = ti.schema
        n = 0
        for row_literals in stmt.rows:
            values = self._row_values(schema, stmt.columns, row_literals)
            self._insert_version(ti, txn, values)
            n += 1
        return Result(message="INSERT", rowcount=n)

    def _row_values(self, schema, columns, literals):
        raw = [lit.value for lit in literals]
        if columns is None:
            if len(raw) != len(schema.columns):
                raise MiniDBError("column count mismatch")
            return raw
        if len(columns) != len(raw):
            raise MiniDBError("column/value count mismatch")
        by_name = dict(zip(columns, raw))
        out = []
        for col in schema.columns:
            out.append(by_name.get(col.name))   # missing columns -> NULL/None
        return out

    def _pk_conflict(self, ti, key, txn):
        """Classify whether inserting `key` would violate primary-key uniqueness.

        Returns:
          "dup"      -- a committed (or this txn's own) live version exists.
          "conflict" -- another *in-flight* transaction is concurrently
                        creating or deleting this key; the outcome is not yet
                        decided, so we must not insert. Surfaced as a retryable
                        WriteConflictError rather than a hard duplicate error.
          None       -- the key is free.

        Treating an in-flight writer conservatively (rather than assuming its
        delete will commit) is what prevents two live versions of the same key
        from coexisting under concurrent insert/delete/update."""
        idx = self.indexes.get(ti.name, {}).get(ti.pk_column)
        heap = self.heaps[ti.name]
        for rid in idx.search(key):
            rec = heap.get_record(rid)
            if rec is None:
                continue
            m = TupleMeta.unpack(rec)
            # Classify the version's creator.
            if m.xmin == txn.txn_id:
                creator = "self"
            elif self.txn_mgr.is_committed(m.xmin):
                creator = "committed"
            elif self.txn_mgr.is_active(m.xmin):
                return "conflict"          # another txn is inserting this key
            else:
                continue                    # creator aborted: version never was
            # Classify the version's deleter.
            if m.xmax == 0:
                deleted = False
            elif m.xmax == txn.txn_id or self.txn_mgr.is_committed(m.xmax):
                deleted = True
            elif self.txn_mgr.is_active(m.xmax):
                return "conflict"          # another txn is deleting this key
            else:
                deleted = False             # deleter aborted: still present
            if not deleted:
                return "dup"
        return None

    def _insert_version(self, ti, txn, values, enforce_unique=True):
        """Physically insert a new row version (xmin=txn) + WAL + index.

        `enforce_unique` is True for a real INSERT. For the new version created
        by an UPDATE it is True only when the primary key actually changes --
        otherwise the row legitimately keeps its key (uniqueness is preserved by
        the supersede + write-conflict mechanism, not by this check)."""
        schema = ti.schema
        heap = self.heaps[ti.name]
        rec = pack_record(TupleMeta(xmin=txn.txn_id, xmax=0), schema, values)
        with self._write_latch:
            if ti.pk_column is not None:
                pk_val = values[schema.index_of(ti.pk_column)]
                if pk_val is None:
                    raise MiniDBError(f"primary key {ti.pk_column} cannot be NULL")
                if enforce_unique:
                    conflict = self._pk_conflict(ti, pk_val, txn)
                    if conflict == "dup":
                        raise MiniDBError(
                            f"duplicate primary key {ti.pk_column}={pk_val}")
                    if conflict == "conflict":
                        raise WriteConflictError(
                            f"concurrent modification of key "
                            f"{ti.pk_column}={pk_val}")
            self._ensure_begin_logged(txn)
            page_id = heap.find_page_with_room(len(rec))
            if page_id is None:
                page_id, page = heap.allocate_page()
                np_lsn = self.wal.log_newpage(txn.txn_id, page_id, ti.table_id)
                page.page_lsn = np_lsn
                self.bp.unpin_page(page_id, True)
            slot = heap.insert_into_page(page_id, rec)
            rid = RID(page_id, slot)
            lsn = self.wal.log_insert(txn.txn_id, page_id, slot, rec)
            heap.stamp_lsn(page_id, lsn)
            txn.undo_log.append(("insert", rid, None))
            if ti.pk_column:
                pk_pos = schema.index_of(ti.pk_column)
                self.indexes[ti.name][ti.pk_column].insert(values[pk_pos], rid)
            self.row_counts[ti.name] = self.row_counts.get(ti.name, 0) + 1
        return rid

    def _supersede(self, ti, txn, rid):
        """Mark the version at rid as deleted by txn (xmax = txn). Used by both
        DELETE and the first half of UPDATE. Detects write-write conflicts."""
        heap = self.heaps[ti.name]
        with self._write_latch:
            self._ensure_begin_logged(txn)
            rec = heap.get_record(rid)
            if rec is None:
                raise WriteConflictError("row vanished")
            meta = TupleMeta.unpack(rec)
            if meta.xmax != 0 and not self._is_dead_writer(meta.xmax):
                # Someone already deleted/updated this version after our snapshot.
                raise WriteConflictError(
                    f"write-write conflict on {rid} (xmax={meta.xmax})")
            before = rec
            new_meta = TupleMeta(xmin=meta.xmin, xmax=txn.txn_id)
            after = record_with_meta(rec, new_meta)
            lsn = self.wal.log_update(txn.txn_id, rid.page_id, rid.slot_id,
                                      before, after)
            heap.update_record_inplace(rid, after, lsn)
            txn.undo_log.append(("update", rid, before))

    def _is_dead_writer(self, txn_id):
        """An xmax left by a transaction that aborted is harmless (the deletion
        never took effect), so it does not constitute a conflict."""
        return not self.txn_mgr.is_committed(txn_id) \
            and not self.txn_mgr.is_active(txn_id)

    # ---- DELETE ---------------------------------------------------------
    def _delete(self, stmt, txn):
        ti = self.catalog.get_table(stmt.table)
        ctx = ExecContext(self, txn)
        rids = self._scan_visible_rids(ti, ctx, stmt.where, for_write=True)
        for rid in rids:
            self._supersede(ti, txn, rid)
        self.row_counts[ti.name] = max(0, self.row_counts.get(ti.name, 0) - len(rids))
        return Result(message="DELETE", rowcount=len(rids))

    # ---- UPDATE ---------------------------------------------------------
    def _update(self, stmt, txn):
        ti = self.catalog.get_table(stmt.table)
        schema = ti.schema
        ctx = ExecContext(self, txn)
        targets = self._scan_visible_rows(ti, ctx, stmt.where, for_write=True)
        assignments = {c: lit.value for c, lit in stmt.assignments}
        for c in assignments:
            schema.index_of(c)   # validate column exists
        pk_pos = schema.index_of(ti.pk_column) if ti.pk_column else None
        n = 0
        for rid, values in targets:
            new_values = list(values)
            for c, v in assignments.items():
                new_values[schema.index_of(c)] = v
            # Only re-check uniqueness if the primary key value actually changed.
            pk_changed = pk_pos is not None and new_values[pk_pos] != values[pk_pos]
            self._supersede(ti, txn, rid)       # retire old version
            self._insert_version(ti, txn, new_values, enforce_unique=pk_changed)
            n += 1
        return Result(message="UPDATE", rowcount=n)

    # ---- helpers for DELETE/UPDATE -------------------------------------
    def _scan_visible_rows(self, ti, ctx, where, for_write=False):
        """Return [(rid, values)] for visible rows matching `where`.

        Uses the primary-key index for an equality predicate on the PK (so a
        point UPDATE/DELETE reads and locks only the target row -- this is what
        gives 2PL its row-level granularity); otherwise scans the heap."""
        schema = ti.schema
        heap = self.heaps[ti.name]
        alias = ti.name
        col_to_alias = {cn: alias for cn in schema.names}

        # Try to find an equality predicate on the PK to drive an index lookup.
        key = None
        idx = self.indexes.get(ti.name, {}).get(ti.pk_column) if ti.pk_column else None
        if idx is not None:
            for c in opt_mod.split_and(where):
                eq = opt_mod.optimizer.eq_col_literal(c, alias, ti, col_to_alias)
                if eq is not None and eq[0] == ti.pk_column:
                    key = eq[1]
                    break

        if key is not None:
            candidate_rids = idx.search(key)
        else:
            candidate_rids = [rid for rid, _ in heap.scan()]

        out = []
        for rid in candidate_rids:
            if for_write:
                # Lock first, then read under the lock, so the version we decide
                # to modify cannot be superseded between the visibility check and
                # the write (which would otherwise be a lost update).
                ctx.lock_write(rid)
            rec = heap.get_record(rid)
            if rec is None:
                continue
            visible, values = ctx.read_row(heap, rid, rec, schema)
            if not visible:
                continue
            row = {f"{alias}.{n}": v for n, v in zip(schema.names, values)}
            if not expr_mod.eval_predicate(row, where):
                continue
            out.append((rid, values))
        return out

    def _scan_visible_rids(self, ti, ctx, where, for_write=False):
        return [rid for rid, _ in self._scan_visible_rows(ti, ctx, where, for_write)]

    # ---- SELECT ---------------------------------------------------------
    def _select(self, stmt, txn):
        ctx = ExecContext(self, txn)
        plan = opt_mod.plan_select(self, ctx, stmt)
        rows = list(plan.root)
        rows = self._apply_aggregation(stmt, rows)
        rows = self._apply_order_by(stmt, rows)
        if stmt.limit is not None:
            rows = rows[:stmt.limit]
        columns, tuples = self._project(stmt, plan, rows)
        return Result(columns=columns, rows=tuples)

    def explain(self, sql, txn=None):
        """Return a human-readable plan for a SELECT (does not execute it)."""
        auto = txn is None
        if auto:
            txn = self.begin_transaction()
        try:
            stmt = parse(sql)
            if not isinstance(stmt, ast.Select):
                raise MiniDBError("EXPLAIN supports SELECT only")
            ctx = ExecContext(self, txn)
            plan = opt_mod.plan_select(self, ctx, stmt)
            text = plan.root.explain()
            if plan.notes:
                text += "\n\nPlanner notes:\n  " + "\n  ".join(plan.notes)
            return text
        finally:
            if auto:
                self.commit(txn)

    # ---- post-processing: aggregation / order / projection -------------
    def _has_aggregates(self, stmt):
        return bool(stmt.group_by) or any(
            isinstance(it, ast.Aggregate) for it in stmt.items)

    def _apply_aggregation(self, stmt, rows):
        if not self._has_aggregates(stmt):
            return rows
        group_refs = stmt.group_by
        groups = {}
        order = []
        for row in rows:
            key = tuple(expr_mod.resolve_column(row, g) for g in group_refs)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)
        if not group_refs and not groups:
            groups[()] = []          # COUNT(*) over empty input -> one row
            order.append(())
        out = []
        for key in order:
            members = groups[key]
            agg_row = {}
            for g, kv in zip(group_refs, key):
                agg_row[g.qualified] = kv
            for it in stmt.items:
                if isinstance(it, ast.Aggregate):
                    label = self._agg_label(it)
                    agg_row[label] = self._compute_aggregate(it, members)
            out.append(agg_row)
        return out

    def _agg_label(self, agg):
        if agg.alias:
            return agg.alias
        arg = "*" if isinstance(agg.arg, ast.Star) else agg.arg.name
        return f"{agg.func.lower()}_{arg}"

    def _compute_aggregate(self, agg, rows):
        if agg.func == "COUNT":
            if isinstance(agg.arg, ast.Star):
                return len(rows)
            return sum(1 for r in rows
                       if expr_mod.resolve_column(r, agg.arg) is not None)
        vals = [expr_mod.resolve_column(r, agg.arg) for r in rows]
        vals = [v for v in vals if v is not None]
        if agg.func == "MIN":
            return min(vals) if vals else None
        if agg.func == "MAX":
            return max(vals) if vals else None
        if agg.func == "SUM":
            return sum(vals) if vals else None
        if agg.func == "AVG":
            return (sum(vals) / len(vals)) if vals else None
        raise MiniDBError(f"unknown aggregate {agg.func}")

    def _lookup(self, row, ref):
        """Flexible column lookup for ORDER BY (handles qualified names, bare
        names and aggregate output labels)."""
        if ref.qualified in row:
            return row[ref.qualified]
        if ref.name in row:
            return row[ref.name]
        for k, v in row.items():
            if "." in k and k.split(".", 1)[1] == ref.name:
                return v
        raise MiniDBError(f"unknown ORDER BY column {ref.qualified}")

    def _apply_order_by(self, stmt, rows):
        if stmt.order_by is None:
            return rows
        ob = stmt.order_by
        # NULL-safe key: group Nones together (they sort first) instead of
        # comparing None against ints.
        def key(r):
            v = self._lookup(r, ob.column)
            return (v is not None, v)
        return sorted(rows, key=key, reverse=ob.desc)

    def _project(self, stmt, plan, rows):
        # Aggregate query: select list is group cols + aggregates.
        if self._has_aggregates(stmt):
            labels = []
            getters = []
            for it in stmt.items:
                if isinstance(it, ast.Aggregate):
                    lab = self._agg_label(it)
                    labels.append(it.alias or lab)
                    getters.append(lambda r, lab=lab: r.get(lab))
                elif isinstance(it, ast.SelectItem):
                    ref = it.expr
                    labels.append(it.alias or ref.name)
                    getters.append(lambda r, ref=ref: self._lookup(r, ref))
                else:
                    raise MiniDBError("SELECT * not allowed with aggregates")
            tuples = [tuple(g(r) for g in getters) for r in rows]
            return labels, tuples

        # Normal projection.
        labels = []
        getters = []
        for it in stmt.items:
            if isinstance(it, ast.Star):
                for alias, ti in plan.sources:
                    if it.table is not None and it.table != alias:
                        continue
                    for cn in ti.schema.names:
                        labels.append(cn)
                        key = f"{alias}.{cn}"
                        getters.append(lambda r, key=key: r.get(key))
            elif isinstance(it, ast.SelectItem):
                ref = it.expr
                labels.append(it.alias or ref.name)
                getters.append(lambda r, ref=ref: expr_mod.resolve_column(r, ref))
            else:
                raise MiniDBError("unexpected select item")
        tuples = [tuple(g(r) for g in getters) for r in rows]
        return labels, tuples

    # ---- statistics (for the optimizer) --------------------------------
    def table_stats(self, name):
        ti = self.catalog.get_table(name)
        row_count = self.row_counts.get(name, 0)
        ndistinct = {}
        if ti.pk_column:
            ndistinct[ti.pk_column] = max(1, row_count)   # pk is unique
        return {"row_count": row_count, "ndistinct": ndistinct}
