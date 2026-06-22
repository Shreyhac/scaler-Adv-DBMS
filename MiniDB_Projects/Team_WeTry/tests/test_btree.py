"""B+ tree tests: ordered structure, point/range search, multi-value, delete."""

import random

from minidb.index.btree import BPlusTree
from minidb.record.rid import RID


def test_point_and_range():
    t = BPlusTree(order=8)
    keys = list(range(500))
    random.shuffle(keys)
    for k in keys:
        t.insert(k, RID(k, 0))
    assert t.num_keys == 500
    assert t.search(250) == [RID(250, 0)]
    assert t.search(9999) == []
    assert [k for k, _ in t.range_scan(100, 109)] == list(range(100, 110))
    assert [k for k, _ in t.range_scan()] == list(range(500))


def test_multi_value_per_key():
    t = BPlusTree(order=4)
    t.insert(1, RID(0, 0))
    t.insert(1, RID(0, 1))      # second version of same key
    t.insert(1, RID(1, 0))
    assert set(t.search(1)) == {RID(0, 0), RID(0, 1), RID(1, 0)}
    assert t.num_keys == 1
    assert len(t) == 3


def test_delete_entry():
    t = BPlusTree(order=4)
    t.insert(5, RID(0, 0))
    t.insert(5, RID(0, 1))
    assert t.delete_entry(5, RID(0, 0)) is True
    assert t.search(5) == [RID(0, 1)]
    assert t.delete_entry(5, RID(0, 1)) is True
    assert t.search(5) == []
    assert t.delete_entry(5, RID(9, 9)) is False
