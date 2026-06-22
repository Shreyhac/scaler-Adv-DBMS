"""Record identifier: the physical address of a record."""

from typing import NamedTuple


class RID(NamedTuple):
    page_id: int
    slot_id: int

    def __repr__(self):
        return f"RID({self.page_id}:{self.slot_id})"
