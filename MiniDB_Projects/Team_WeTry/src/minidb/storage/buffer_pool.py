"""Buffer pool: a fixed set of page frames cached in front of the disk manager.

Callers pin a page with fetch_page, use it, then unpin_page (flagging it dirty
if changed). A full pool evicts an unpinned page using LRU, writing it back if
dirty. Before writing any dirty page we flush the WAL (the write-ahead rule):
the log record for a change is durable before the changed page reaches disk.
"""

import threading
from collections import OrderedDict

from ..config import BUFFER_POOL_SIZE


class _Frame:
    __slots__ = ("page", "pin_count", "dirty")

    def __init__(self, page):
        self.page = page
        self.pin_count = 0
        self.dirty = False


class BufferPool:
    def __init__(self, disk_manager, pool_size=BUFFER_POOL_SIZE, wal_flush=None):
        self.disk = disk_manager
        self.pool_size = pool_size
        self.wal_flush = wal_flush or (lambda: None)
        # page_id -> _Frame. OrderedDict gives us LRU ordering for free.
        self._frames = OrderedDict()
        # Physical latch over the frame table, pin counts and the disk file
        # handle. Held only for the duration of a buffer/disk operation, not
        # across a transaction, so MVCC reads stay effectively lock-free. A
        # pinned page can't be evicted, so the page fetch_page returns stays
        # valid until the caller unpins it.
        self._latch = threading.RLock()

    # ---- core API -------------------------------------------------------
    def fetch_page(self, page_id):
        with self._latch:
            frame = self._frames.get(page_id)
            if frame is not None:
                self._frames.move_to_end(page_id)  # mark most-recently-used
                frame.pin_count += 1
                return frame.page
            self._make_room()
            page = self.disk.read_page(page_id)
            frame = _Frame(page)
            frame.pin_count = 1
            self._frames[page_id] = frame
            return page

    def new_page(self):
        """Allocate a fresh page on disk, load and pin it."""
        from .page import Page
        with self._latch:
            page_id = self.disk.allocate_page()
            self._make_room()
            page = Page(page_id)  # a clean, zero-initialised slotted page
            frame = _Frame(page)
            frame.pin_count = 1
            self._frames[page_id] = frame
            return page

    def unpin_page(self, page_id, is_dirty):
        with self._latch:
            frame = self._frames.get(page_id)
            if frame is None:
                return
            if is_dirty:
                frame.dirty = True
            if frame.pin_count > 0:
                frame.pin_count -= 1

    def flush_page(self, page_id):
        with self._latch:
            frame = self._frames.get(page_id)
            if frame is None:
                return
            if frame.dirty:
                self.wal_flush()  # WAL before data
                self.disk.write_page(frame.page)
                frame.dirty = False

    def flush_all(self):
        with self._latch:
            for page_id in list(self._frames.keys()):
                self.flush_page(page_id)

    # ---- eviction (caller holds the latch) -----------------------------
    def _make_room(self):
        if len(self._frames) < self.pool_size:
            return
        # Evict the least-recently-used unpinned frame.
        for page_id, frame in list(self._frames.items()):
            if frame.pin_count == 0:
                if frame.dirty:
                    self.wal_flush()
                    self.disk.write_page(frame.page)
                del self._frames[page_id]
                return
        raise RuntimeError("buffer pool full: all pages are pinned")
