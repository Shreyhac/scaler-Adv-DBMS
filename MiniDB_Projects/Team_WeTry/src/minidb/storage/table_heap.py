"""Table heap: a table's records spread across the pages tagged with its
table_id. The page list is kept in memory and rebuilt at startup by scanning,
so the on-disk format needs no page linked list. The heap deals only in raw
record bytes and RIDs; visibility, locking and WAL logging live in higher
layers.
"""

from ..record.rid import RID


class TableHeap:
    def __init__(self, buffer_pool, table_id):
        self.bp = buffer_pool
        self.table_id = table_id
        # In-memory cache of page ids owned by this table (an optimisation; the
        # authoritative info is each page's header.table_id).
        self._page_ids = []
        self._rebuild_page_list()

    def _rebuild_page_list(self):
        self._page_ids = []
        for page_id in range(self.bp.disk.num_pages()):
            page = self.bp.fetch_page(page_id)
            try:
                if page.table_id == self.table_id:
                    self._page_ids.append(page_id)
            finally:
                self.bp.unpin_page(page_id, False)

    # ---- physical primitives (used by engine and recovery) -------------
    def allocate_page(self):
        """Allocate a fresh page, tag it for this table, return (page_id, Page).
        The returned page is pinned; caller must unpin."""
        page = self.bp.new_page()
        page.init_as_table_page(self.table_id)
        self._page_ids.append(page.page_id)
        return page.page_id, page

    def insert_into_page(self, page_id, record_bytes, lsn=None):
        """Insert into a specific page; return slot_id or None if it won't fit.
        Page is fetched and unpinned here (marked dirty on success). If `lsn` is
        given, stamp it as the page's page_lsn for recovery."""
        page = self.bp.fetch_page(page_id)
        try:
            slot_id = page.insert_tuple(record_bytes)
            if slot_id is not None and lsn is not None:
                page.page_lsn = lsn
            self.bp.unpin_page(page_id, slot_id is not None)
            return slot_id
        except Exception:
            self.bp.unpin_page(page_id, False)
            raise

    def stamp_lsn(self, page_id, lsn):
        page = self.bp.fetch_page(page_id)
        try:
            if lsn > page.page_lsn:
                page.page_lsn = lsn
            self.bp.unpin_page(page_id, True)
        except Exception:
            self.bp.unpin_page(page_id, False)
            raise

    def find_page_with_room(self, record_len):
        """Return the id of a page with room for record_len bytes, or None if a
        new page must be allocated. We check the most-recently-used pages first
        (newest pages tend to have room); allocation is left to the caller so it
        can WAL-log it."""
        from .page import SLOT_SIZE
        needed = record_len + SLOT_SIZE
        for page_id in reversed(self._page_ids):
            page = self.bp.fetch_page(page_id)
            try:
                if page.free_space() >= needed:
                    return page_id
            finally:
                self.bp.unpin_page(page_id, False)
        return None

    def find_room(self, record_len):
        """Convenience used by low-level tests: find or allocate a page."""
        page_id = self.find_page_with_room(record_len)
        if page_id is None:
            page_id, _ = self.allocate_page()
            self.bp.unpin_page(page_id, True)
        return page_id

    def get_record(self, rid):
        """Return record bytes at rid, or None if dead/missing."""
        page = self.bp.fetch_page(rid.page_id)
        try:
            return page.get_tuple(rid.slot_id)
        finally:
            self.bp.unpin_page(rid.page_id, False)

    def update_record_inplace(self, rid, record_bytes, lsn=None):
        """Same-length in-place update (e.g. changing the version header)."""
        page = self.bp.fetch_page(rid.page_id)
        try:
            ok = page.update_tuple(rid.slot_id, record_bytes)
            if ok and lsn is not None:
                page.page_lsn = lsn
            self.bp.unpin_page(rid.page_id, ok)
            return ok
        except Exception:
            self.bp.unpin_page(rid.page_id, False)
            raise

    def scan(self):
        """Yield (RID, record_bytes) for every live record in the heap."""
        for page_id in list(self._page_ids):
            page = self.bp.fetch_page(page_id)
            try:
                for slot_id, rec in page.iter_slots():
                    yield RID(page_id, slot_id), rec
            finally:
                self.bp.unpin_page(page_id, False)
