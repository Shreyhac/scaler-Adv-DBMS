"""Disk manager: the only component that touches the database file directly.

The database is a single file viewed as an array of fixed-size pages. Page i
lives at byte offset i * PAGE_SIZE. The disk manager hides this addressing and
exposes page-granular read/write/allocate operations.
"""

import os

from ..config import PAGE_SIZE
from .page import Page


class DiskManager:
    def __init__(self, db_path):
        self.db_path = db_path
        # Open for read+write, creating if necessary. Binary mode.
        if not os.path.exists(db_path):
            open(db_path, "wb").close()
        self._f = open(db_path, "r+b")

    def num_pages(self):
        self._f.seek(0, os.SEEK_END)
        size = self._f.tell()
        return size // PAGE_SIZE

    def allocate_page(self):
        """Grow the file by one page and return the new page id."""
        page_id = self.num_pages()
        self._f.seek(page_id * PAGE_SIZE)
        self._f.write(bytes(PAGE_SIZE))
        self._f.flush()
        return page_id

    def read_page(self, page_id):
        self._f.seek(page_id * PAGE_SIZE)
        data = self._f.read(PAGE_SIZE)
        if len(data) < PAGE_SIZE:
            # Reading a page past EOF (e.g. freshly allocated): pad with zeros.
            data = data + bytes(PAGE_SIZE - len(data))
        return Page(page_id, data)

    def write_page(self, page):
        self._f.seek(page.page_id * PAGE_SIZE)
        self._f.write(bytes(page.data))
        self._f.flush()

    def ensure_page(self, page_id):
        """Make sure `page_id` exists on disk (used during recovery redo when a
        page referenced by the log was never flushed before the crash)."""
        while self.num_pages() <= page_id:
            self.allocate_page()

    def close(self):
        if self._f and not self._f.closed:
            self._f.flush()
            os.fsync(self._f.fileno())
            self._f.close()
