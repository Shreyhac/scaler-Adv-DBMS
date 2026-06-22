"""Crash recovery: a single redo (repeat-history) pass over the WAL.

Unlike classical ARIES we do not physically undo losers. On a versioned heap
that step is actually wrong: if a loser marks a row deleted (xmax=loser) and a
later committed txn re-supersedes it, writing the loser's before-image back
would resurrect the row. Instead we lean on MVCC visibility -- a loser's
created versions (xmin=loser) and deletions (xmax=loser) are never committed, so
they are invisible to everyone automatically.

So recovery replays every NEWPAGE/INSERT/UPDATE in LSN order to rebuild the
exact crash-time bytes (guarded by page_lsn for idempotency), and the recovered
committed set -- with losers excluded -- makes the runtime visibility rule do
the rest. Redo is idempotent, so a crash during recovery is safe.
"""

from .wal import BEGIN, INSERT, UPDATE, COMMIT, NEWPAGE


class RecoveryResult:
    def __init__(self, committed, losers, max_txn_id):
        self.committed = committed
        self.losers = losers
        self.max_txn_id = max_txn_id


def recover(buffer_pool, wal):
    records = list(wal.read_all())
    if not records:
        return RecoveryResult(set(), set(), 0)

    # ---- analysis: winners (committed) vs losers (started, never committed) --
    started, committed = set(), set()
    max_txn_id = 0
    for r in records:
        if r.txn_id:
            max_txn_id = max(max_txn_id, r.txn_id)
        if r.type == BEGIN:
            started.add(r.txn_id)
        elif r.type == COMMIT:
            committed.add(r.txn_id)
    losers = started - committed

    disk = buffer_pool.disk

    # ---- redo (repeat history) -----------------------------------------
    # Replay everything -- winners and losers alike -- so the heap bytes match
    # the crash-time state. Visibility (via the committed set) decides what is
    # actually observable; losers' versions are present but invisible.
    for r in records:
        if r.type == NEWPAGE:
            disk.ensure_page(r.page_id)
            page = buffer_pool.fetch_page(r.page_id)
            dirty = False
            if page.page_lsn < r.lsn:
                page.init_as_table_page(r.table_id)
                page.page_lsn = r.lsn
                dirty = True
            buffer_pool.unpin_page(r.page_id, dirty)
        elif r.type == INSERT:
            disk.ensure_page(r.page_id)
            page = buffer_pool.fetch_page(r.page_id)
            dirty = False
            if page.page_lsn < r.lsn:
                slot = page.insert_tuple(r.after)
                # The page was replayed up to just before this insert, so the
                # next free slot must match the slot recorded in the log.
                assert slot == r.slot_id, (
                    f"redo slot mismatch: got {slot}, expected {r.slot_id}")
                page.page_lsn = r.lsn
                dirty = True
            buffer_pool.unpin_page(r.page_id, dirty)
        elif r.type == UPDATE:
            disk.ensure_page(r.page_id)
            page = buffer_pool.fetch_page(r.page_id)
            dirty = False
            if page.page_lsn < r.lsn:
                page.update_tuple(r.slot_id, r.after)
                page.page_lsn = r.lsn
                dirty = True
            buffer_pool.unpin_page(r.page_id, dirty)

    buffer_pool.flush_all()
    return RecoveryResult(committed, losers, max_txn_id)
