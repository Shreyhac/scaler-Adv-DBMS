"""Storage-layer tests: slotted page, disk manager, buffer pool, table heap."""

import os

from minidb.storage.disk_manager import DiskManager
from minidb.storage.buffer_pool import BufferPool
from minidb.storage.table_heap import TableHeap
from minidb.storage.page import Page
from minidb.record.schema import Schema, Column
from minidb.record.tuple import TupleMeta, pack_record, unpack_record
from minidb.record.rid import RID


def test_page_insert_get_delete():
    p = Page(0)
    p.init_as_table_page(1)
    s0 = p.insert_tuple(b"hello")
    s1 = p.insert_tuple(b"world!!")
    assert p.get_tuple(s0) == b"hello"
    assert p.get_tuple(s1) == b"world!!"
    assert p.delete_tuple(s0) is True
    assert p.get_tuple(s0) is None
    # slot ids stay stable after delete
    assert p.get_tuple(s1) == b"world!!"


def test_page_full():
    p = Page(0)
    n = 0
    while p.insert_tuple(b"x" * 64) is not None:
        n += 1
    assert n > 0
    assert p.insert_tuple(b"x" * 64) is None  # no room


def test_heap_persistence(tmp_path):
    path = str(tmp_path / "h.data")
    dm = DiskManager(path)
    bp = BufferPool(dm, pool_size=4)
    heap = TableHeap(bp, table_id=1)
    sch = Schema([Column("id", "INT"), Column("name", "TEXT")])

    rids = []
    for i in range(300):
        rec = pack_record(TupleMeta(xmin=1), sch, [i, f"n{i}"])
        pid = heap.find_room(len(rec))
        slot = heap.insert_into_page(pid, rec)
        rids.append(RID(pid, slot))
    bp.flush_all()
    dm.close()

    # reopen; page list rebuilt purely from on-disk table_id tags
    dm2 = DiskManager(path)
    bp2 = BufferPool(dm2, pool_size=4)
    heap2 = TableHeap(bp2, table_id=1)
    seen = set()
    for _rid, rec in heap2.scan():
        _m, vals = unpack_record(rec, sch)
        seen.add(vals[0])
    assert seen == set(range(300))
    dm2.close()


def test_buffer_pool_eviction(tmp_path):
    dm = DiskManager(str(tmp_path / "b.data"))
    bp = BufferPool(dm, pool_size=2)
    # allocate more pages than frames; unpin so they can be evicted
    ids = []
    for _ in range(5):
        pg = bp.new_page()
        pg.init_as_table_page(1)
        pg.insert_tuple(b"data")
        ids.append(pg.page_id)
        bp.unpin_page(pg.page_id, True)
    bp.flush_all()
    # every page should be readable back with its record
    for pid in ids:
        pg = bp.fetch_page(pid)
        assert pg.get_tuple(0) == b"data"
        bp.unpin_page(pid, False)
    dm.close()
