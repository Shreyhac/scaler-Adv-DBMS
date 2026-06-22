"""Transaction / concurrency tests for both MVCC and 2PL modes."""

import threading

from minidb.txn.lock_manager import DeadlockError
from minidb.engine import WriteConflictError


def test_mvcc_snapshot_isolation(tmpdb):
    db = tmpdb("mvcc")
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    db.execute("INSERT INTO t VALUES (1,10)")

    reader = db.begin_transaction()           # snapshot: v=10
    writer = db.begin_transaction()
    db.execute("UPDATE t SET v=99 WHERE id=1", txn=writer)
    db.commit(writer)

    # reader keeps its snapshot -> repeatable read
    r = db.execute("SELECT v FROM t WHERE id=1", txn=reader)
    assert r.rows == [(10,)]
    db.commit(reader)

    # a new transaction sees the committed value
    r = db.execute("SELECT v FROM t WHERE id=1")
    assert r.rows == [(99,)]
    db.close()


def test_mvcc_write_write_conflict(tmpdb):
    db = tmpdb("mvcc")
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    db.execute("INSERT INTO t VALUES (1,0)")
    a = db.begin_transaction()
    b = db.begin_transaction()
    db.execute("UPDATE t SET v=1 WHERE id=1", txn=a)
    raised = False
    try:
        db.execute("UPDATE t SET v=2 WHERE id=1", txn=b)
    except WriteConflictError:
        raised = True
    assert raised
    db.commit(a)
    db.abort(b)
    db.close()


def test_abort_rolls_back(tmpdb):
    db = tmpdb("mvcc")
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    db.execute("INSERT INTO t VALUES (1,1)")
    t = db.begin_transaction()
    db.execute("INSERT INTO t VALUES (2,2)", txn=t)
    db.execute("UPDATE t SET v=100 WHERE id=1", txn=t)
    db.abort(t)
    r = db.execute("SELECT id, v FROM t ORDER BY id")
    assert r.rows == [(1, 1)]      # insert + update both undone
    db.close()


def test_2pl_deadlock_detection(tmpdb):
    db = tmpdb("2pl")
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    db.execute("INSERT INTO t VALUES (1,0),(2,0)")

    results = {}
    barrier = threading.Barrier(2)

    def worker(name, first, second):
        t = db.begin_transaction()
        try:
            db.execute(f"UPDATE t SET v=1 WHERE id={first}", txn=t)
            barrier.wait()
            db.execute(f"UPDATE t SET v=1 WHERE id={second}", txn=t)
            db.commit(t)
            results[name] = "committed"
        except DeadlockError:
            db.abort(t)
            results[name] = "deadlock"

    ta = threading.Thread(target=worker, args=("A", 1, 2))
    tb = threading.Thread(target=worker, args=("B", 2, 1))
    ta.start(); tb.start(); ta.join(10); tb.join(10)
    assert "deadlock" in results.values()
    assert "committed" in results.values()
    db.close()
