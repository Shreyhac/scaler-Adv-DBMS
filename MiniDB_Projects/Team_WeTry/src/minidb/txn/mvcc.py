"""MVCC visibility rule (Track B extension).

A version (xmin, xmax) is visible to a transaction iff its creator is visible
(xmin is the txn itself, or in the set of committed ids it may see) and its
deleter is not (xmax is 0, or the deleter is neither the txn nor in that set).
`visible_set` is the frozen BEGIN snapshot under MVCC, or the live committed set
under 2PL (where locks, not versioning, prevent other anomalies).
"""


def is_visible(meta, txn_id, visible_set):
    creator_visible = (meta.xmin == txn_id) or (meta.xmin in visible_set)
    if not creator_visible:
        return False
    if meta.xmax == 0:
        return True
    deleter_visible = (meta.xmax == txn_id) or (meta.xmax in visible_set)
    return not deleter_visible
