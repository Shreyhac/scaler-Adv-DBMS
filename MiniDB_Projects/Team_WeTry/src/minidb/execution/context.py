"""Execution context: how a statement reads rows under the active CC mode.

MVCC reads are lock-free and judged against the snapshot frozen at BEGIN. 2PL
reads take a shared lock on each candidate version, re-read it under that lock,
and judge against the live committed set -- re-reading under the lock is what
stops a reader observing a value a concurrent committed writer has already
superseded (a stale read / lost update).
"""

from ..txn.mvcc import is_visible
from ..record.tuple import unpack_record


class ExecContext:
    def __init__(self, engine, txn):
        self.engine = engine
        self.txn = txn
        self.mode = engine.mode
        self.lock_mgr = engine.lock_mgr
        # MVCC reads against a snapshot frozen at BEGIN; 2PL re-reads the live
        # committed set on every access (see read_row).
        self._snapshot = txn.snapshot if self.mode == "mvcc" else None

    def read_row(self, heap, rid, rec, schema):
        """Return (visible, values) for a candidate version.

        `rec` is the record bytes the scan already has. In MVCC we use them as
        is; in 2PL we lock the row and re-read it under the lock before judging
        visibility against the current committed set."""
        if self.mode == "2pl":
            self.lock_mgr.acquire_shared(self.txn.txn_id, rid)
            rec = heap.get_record(rid)           # re-read under the lock
            if rec is None:
                return False, None
            visible_set = self.engine.txn_mgr.committed_snapshot()
        else:
            visible_set = self._snapshot
        meta, values = unpack_record(rec, schema)
        return is_visible(meta, self.txn.txn_id, visible_set), values

    def lock_write(self, rid):
        if self.mode == "2pl":
            self.lock_mgr.acquire_exclusive(self.txn.txn_id, rid)
