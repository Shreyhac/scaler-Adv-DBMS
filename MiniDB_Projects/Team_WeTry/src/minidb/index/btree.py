"""B+ tree index mapping an integer key -> one or more RIDs.

Internal nodes hold separator keys and child pointers; leaves hold sorted keys
with their RID lists and are chained via `next` for range scans. Insert splits
bottom-up on overflow.

The index maps a key to a *list* of RIDs because, under MVCC, an UPDATE creates
a new row version (new RID) while the old one survives for older snapshots --
both share the key. A lookup returns every candidate RID and the executor
applies visibility to pick the right version.

It is memory-resident and rebuilt from the heap at startup, so it is not itself
WAL-logged (disk-backed index pages are future work, per the README).
"""

import bisect
import threading


class _Node:
    __slots__ = ("leaf", "keys", "children", "values", "next")

    def __init__(self, leaf):
        self.leaf = leaf
        self.keys = []          # separator keys (internal) / sorted keys (leaf)
        self.children = []      # child _Node pointers (internal only)
        self.values = []        # list-of-RIDs per key (leaf only)
        self.next = None        # next leaf to the right (leaf only)


class BPlusTree:
    def __init__(self, order=64):
        assert order >= 3
        self.order = order
        self.root = _Node(leaf=True)
        self._num_keys = 0
        self._num_entries = 0
        # Short latch so a traversal never observes a half-finished split.
        # Held only for the O(log N) operation, never across a transaction.
        self._latch = threading.RLock()

    def __len__(self):
        return self._num_entries

    @property
    def num_keys(self):
        return self._num_keys

    # ---- search ---------------------------------------------------------
    def _find_leaf(self, key):
        node = self.root
        while not node.leaf:
            i = bisect.bisect_right(node.keys, key)
            node = node.children[i]
        return node

    def search(self, key):
        """Return the list of RIDs stored under key (empty list if absent)."""
        with self._latch:
            leaf = self._find_leaf(key)
            i = bisect.bisect_left(leaf.keys, key)
            if i < len(leaf.keys) and leaf.keys[i] == key:
                return list(leaf.values[i])
            return []

    def range_scan(self, low=None, high=None):
        """Return [(key, RID)] for low <= key <= high, ascending. None bounds
        are unbounded. Materialised under the latch (rather than yielded lazily)
        so the snapshot of matches is consistent with concurrent writers."""
        with self._latch:
            out = []
            node = self.root
            while not node.leaf:
                i = 0 if low is None else bisect.bisect_right(node.keys, low)
                node = node.children[i]
            while node is not None:
                for k, rids in zip(node.keys, node.values):
                    if low is not None and k < low:
                        continue
                    if high is not None and k > high:
                        return out
                    for rid in rids:
                        out.append((k, rid))
                node = node.next
            return out

    # ---- insert ---------------------------------------------------------
    def insert(self, key, rid):
        """Add rid under key (appending if the key already exists)."""
        with self._latch:
            self._num_entries += 1
            result = self._insert(self.root, key, rid)
            if result is not None:
                sep_key, right = result
                new_root = _Node(leaf=False)
                new_root.keys = [sep_key]
                new_root.children = [self.root, right]
                self.root = new_root

    def _insert(self, node, key, rid):
        if node.leaf:
            i = bisect.bisect_left(node.keys, key)
            if i < len(node.keys) and node.keys[i] == key:
                node.values[i].append(rid)      # additional version
                return None
            node.keys.insert(i, key)
            node.values.insert(i, [rid])
            self._num_keys += 1
            if len(node.keys) >= self.order:
                return self._split_leaf(node)
            return None
        i = bisect.bisect_right(node.keys, key)
        result = self._insert(node.children[i], key, rid)
        if result is None:
            return None
        sep_key, right = result
        node.keys.insert(i, sep_key)
        node.children.insert(i + 1, right)
        if len(node.keys) >= self.order:
            return self._split_internal(node)
        return None

    def _split_leaf(self, node):
        mid = len(node.keys) // 2
        right = _Node(leaf=True)
        right.keys = node.keys[mid:]
        right.values = node.values[mid:]
        node.keys = node.keys[:mid]
        node.values = node.values[:mid]
        right.next = node.next
        node.next = right
        return right.keys[0], right        # smallest right key copied up

    def _split_internal(self, node):
        mid = len(node.keys) // 2
        sep_key = node.keys[mid]            # pushed up (not copied)
        right = _Node(leaf=False)
        right.keys = node.keys[mid + 1:]
        right.children = node.children[mid + 1:]
        node.keys = node.keys[:mid]
        node.children = node.children[:mid + 1]
        return sep_key, right

    # ---- delete ---------------------------------------------------------
    def delete_entry(self, key, rid):
        """Remove one (key, rid) entry. Returns True if removed.
        (No node merging/rebalancing -- see module docstring.)"""
        with self._latch:
            leaf = self._find_leaf(key)
            i = bisect.bisect_left(leaf.keys, key)
            if i < len(leaf.keys) and leaf.keys[i] == key:
                try:
                    leaf.values[i].remove(rid)
                except ValueError:
                    return False
                self._num_entries -= 1
                if not leaf.values[i]:
                    leaf.keys.pop(i)
                    leaf.values.pop(i)
                    self._num_keys -= 1
                return True
            return False

    # ---- introspection --------------------------------------------------
    def height(self):
        h = 1
        node = self.root
        while not node.leaf:
            h += 1
            node = node.children[0]
        return h
