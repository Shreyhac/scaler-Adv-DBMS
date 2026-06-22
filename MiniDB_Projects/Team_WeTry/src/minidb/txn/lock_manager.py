"""Strict Two-Phase Locking (the 2PL core).

Shared (S) and exclusive (X) locks per RID: S/S compatible, X conflicts with
everything, and a lone S can upgrade to X. All locks are held until
commit/abort and released together, giving serializable isolation with no
cascading aborts.

Deadlocks: before blocking, a transaction records what it waits for in a
wait-for graph; if that would close a cycle, the requester is aborted with
DeadlockError. MVCC mode bypasses this manager entirely (lock-free reads).
"""

import threading


class DeadlockError(Exception):
    pass


class LockManager:
    def __init__(self):
        self._cv = threading.Condition()
        # rid -> {"X": Optional[txn_id], "S": set(txn_id)}
        self._table = {}
        # txn_id -> set(txn_id) it is currently blocked waiting on
        self._waits_for = {}

    # ---- public API -----------------------------------------------------
    def acquire_shared(self, txn_id, rid):
        self._acquire(txn_id, rid, "S")

    def acquire_exclusive(self, txn_id, rid):
        self._acquire(txn_id, rid, "X")

    def release_all(self, txn_id):
        with self._cv:
            for entry in self._table.values():
                if entry["X"] == txn_id:
                    entry["X"] = None
                entry["S"].discard(txn_id)
            self._waits_for.pop(txn_id, None)
            self._cv.notify_all()

    # ---- internals ------------------------------------------------------
    def _acquire(self, txn_id, rid, mode):
        with self._cv:
            while True:
                if self._can_grant(txn_id, rid, mode):
                    self._grant(txn_id, rid, mode)
                    self._waits_for.pop(txn_id, None)
                    self._cv.notify_all()
                    return
                blockers = self._conflicting_holders(txn_id, rid, mode)
                self._waits_for[txn_id] = blockers
                if self._creates_cycle(txn_id):
                    self._waits_for.pop(txn_id, None)
                    raise DeadlockError(
                        f"deadlock: txn {txn_id} waiting on {sorted(blockers)}")
                self._cv.wait()

    def _entry(self, rid):
        e = self._table.get(rid)
        if e is None:
            e = {"X": None, "S": set()}
            self._table[rid] = e
        return e

    def _can_grant(self, txn_id, rid, mode):
        e = self._table.get(rid)
        if e is None:
            return True
        if mode == "S":
            return e["X"] in (None, txn_id)
        # mode == "X"
        x_ok = e["X"] in (None, txn_id)
        s_ok = e["S"] <= {txn_id}
        return x_ok and s_ok

    def _grant(self, txn_id, rid, mode):
        e = self._entry(rid)
        if mode == "S":
            if e["X"] != txn_id:        # don't downgrade an existing X
                e["S"].add(txn_id)
        else:
            e["X"] = txn_id
            e["S"].discard(txn_id)      # upgrade: drop the S we held

    def _conflicting_holders(self, txn_id, rid, mode):
        e = self._table.get(rid)
        if e is None:
            return set()
        holders = set()
        if e["X"] is not None and e["X"] != txn_id:
            holders.add(e["X"])
        if mode == "X":
            holders |= (e["S"] - {txn_id})
        return holders

    def _creates_cycle(self, start):
        """Return True if following wait-for edges from `start` loops back."""
        stack = [start]
        seen = set()
        while stack:
            node = stack.pop()
            for nxt in self._waits_for.get(node, ()):  # who `node` waits on
                if nxt == start:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False
