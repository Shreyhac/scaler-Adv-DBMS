"""Transactions and the transaction manager.

Each transaction gets a monotonically increasing id used both as its version
stamp (xmin/xmax) and its identity. The manager tracks the set of COMMITTED
ids, which drives visibility: MVCC freezes a copy of it at BEGIN (snapshot
isolation); 2PL reads the live set and relies on S/X locks for isolation.
"""

import threading

ACTIVE = "ACTIVE"
COMMITTED = "COMMITTED"
ABORTED = "ABORTED"


class Transaction:
    def __init__(self, txn_id, snapshot):
        self.txn_id = txn_id
        self.state = ACTIVE
        # Frozen copy of committed txn ids at BEGIN (used by MVCC visibility).
        self.snapshot = snapshot
        # Locks currently held (2PL): set of ("S"/"X", rid).
        self.locks = set()
        # Undo information for runtime rollback: list of (kind, rid, before).
        #   kind == "insert" -> undo by marking the slot dead
        #   kind == "update" -> undo by writing `before` back in place
        self.undo_log = []

    def __repr__(self):
        return f"Txn({self.txn_id},{self.state})"


class TransactionManager:
    def __init__(self):
        self._next_id = 1
        self._committed = set()
        self._active = set()
        self._lock = threading.RLock()

    def begin(self):
        with self._lock:
            txn_id = self._next_id
            self._next_id += 1
            snapshot = frozenset(self._committed)
            self._active.add(txn_id)
            return Transaction(txn_id, snapshot)

    def commit(self, txn):
        with self._lock:
            txn.state = COMMITTED
            self._active.discard(txn.txn_id)
            self._committed.add(txn.txn_id)

    def abort(self, txn):
        with self._lock:
            txn.state = ABORTED
            self._active.discard(txn.txn_id)
            # Never added to committed -> its versions are invisible to everyone.

    def committed_snapshot(self):
        """Live committed set (used by 2PL reads)."""
        with self._lock:
            return frozenset(self._committed)

    def is_committed(self, txn_id):
        with self._lock:
            return txn_id in self._committed

    def is_active(self, txn_id):
        with self._lock:
            return txn_id in self._active

    def restore(self, committed, next_id):
        """Reinstate committed-set and id counter after crash recovery."""
        with self._lock:
            self._committed = set(committed)
            self._next_id = max(self._next_id, next_id)
