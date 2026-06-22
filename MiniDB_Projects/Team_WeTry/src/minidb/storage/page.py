"""Slotted-page layout for variable-length records.

A page is a fixed-size (PAGE_SIZE) block. A slot directory grows downward from
the header while record payloads grow upward from the end; free space is the
gap between them:

    +-------------------------------------------------------------+
    | header | slot[0] slot[1] ... slot[n-1] -->  free  <-- data  |
    +-------------------------------------------------------------+

Header fields:
* num_slots - slot-directory entries (some may be dead).
* free_ptr  - offset of the most-recent payload (data grows down).
* table_id  - owning table (0 == unallocated). A heap finds its pages by this
  tag rather than an on-disk page list, which keeps the format crash-friendly.
* page_lsn  - LSN of the last change applied here; lets recovery skip a log
  record already reflected on the page, making redo idempotent.

A dead slot has length 0; slot ids stay stable so RIDs (page_id, slot_id) that
the index and WAL hold remain valid after a record is removed.
"""

import struct

from ..config import PAGE_SIZE

# num_slots (uint32), free_ptr (uint32), table_id (int32), page_lsn (uint64)
_HEADER_FORMAT = "<IIiQ"
HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)

# Each slot entry: offset (uint16), length (uint16)
_SLOT_FORMAT = "<HH"
SLOT_SIZE = struct.calcsize(_SLOT_FORMAT)


class Page:
    """A mutable, in-memory view over a single page's bytes."""

    def __init__(self, page_id, data=None):
        self.page_id = page_id
        if data is None:
            self.data = bytearray(PAGE_SIZE)
            self._set_header(0, PAGE_SIZE, 0, 0)
        else:
            assert len(data) == PAGE_SIZE, "page data must be exactly PAGE_SIZE"
            self.data = bytearray(data)

    # ---- header helpers -------------------------------------------------
    def _get_header(self):
        return struct.unpack_from(_HEADER_FORMAT, self.data, 0)

    def _set_header(self, num_slots, free_ptr, table_id, page_lsn):
        struct.pack_into(_HEADER_FORMAT, self.data, 0,
                         num_slots, free_ptr, table_id, page_lsn)

    @property
    def num_slots(self):
        return self._get_header()[0]

    @property
    def free_ptr(self):
        return self._get_header()[1]

    @property
    def table_id(self):
        return self._get_header()[2]

    @table_id.setter
    def table_id(self, value):
        ns, fp, _, lsn = self._get_header()
        self._set_header(ns, fp, value, lsn)

    @property
    def page_lsn(self):
        return self._get_header()[3]

    @page_lsn.setter
    def page_lsn(self, value):
        ns, fp, tid, _ = self._get_header()
        self._set_header(ns, fp, tid, value)

    def init_as_table_page(self, table_id):
        """(Re)initialise this page as an empty page belonging to table_id."""
        self._set_header(0, PAGE_SIZE, table_id, 0)

    # ---- slot helpers ---------------------------------------------------
    def _slot_pos(self, slot_id):
        return HEADER_SIZE + slot_id * SLOT_SIZE

    def _get_slot(self, slot_id):
        return struct.unpack_from(_SLOT_FORMAT, self.data, self._slot_pos(slot_id))

    def _set_slot(self, slot_id, offset, length):
        struct.pack_into(_SLOT_FORMAT, self.data, self._slot_pos(slot_id), offset, length)

    def free_space(self):
        ns, free_ptr, _, _ = self._get_header()
        slot_array_end = HEADER_SIZE + ns * SLOT_SIZE
        return free_ptr - slot_array_end

    # ---- record operations ---------------------------------------------
    def insert_tuple(self, record_bytes):
        """Insert raw record bytes; return slot id, or None if it doesn't fit."""
        ns, free_ptr, table_id, lsn = self._get_header()
        length = len(record_bytes)
        if self.free_space() < length + SLOT_SIZE:
            return None
        new_offset = free_ptr - length
        self.data[new_offset:free_ptr] = record_bytes
        slot_id = ns
        self._set_slot(slot_id, new_offset, length)
        self._set_header(ns + 1, new_offset, table_id, lsn)
        return slot_id

    def get_tuple(self, slot_id):
        """Return record bytes for slot_id, or None if out of range / dead."""
        if slot_id < 0 or slot_id >= self.num_slots:
            return None
        offset, length = self._get_slot(slot_id)
        if length == 0:
            return None
        return bytes(self.data[offset:offset + length])

    def update_tuple(self, slot_id, record_bytes):
        """In-place update; valid only when the new payload has the SAME length
        as the existing one (used for version-metadata changes like setting
        xmax). Returns True on success."""
        if slot_id < 0 or slot_id >= self.num_slots:
            return False
        offset, length = self._get_slot(slot_id)
        if length != len(record_bytes):
            return False
        self.data[offset:offset + length] = record_bytes
        return True

    def delete_tuple(self, slot_id):
        """Mark a slot dead (length 0); keeps slot id stable. Used by recovery
        to undo an insert."""
        if slot_id < 0 or slot_id >= self.num_slots:
            return False
        offset, _ = self._get_slot(slot_id)
        self._set_slot(slot_id, offset, 0)
        return True

    def iter_slots(self):
        """Yield (slot_id, record_bytes) for every live slot."""
        for slot_id in range(self.num_slots):
            rec = self.get_tuple(slot_id)
            if rec is not None:
                yield slot_id, rec
