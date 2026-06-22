"""Crash recovery tests: committed work survives, uncommitted work is undone."""

from minidb.engine import Engine


def test_recovery_after_crash(tmp_path):
    d = str(tmp_path / "db")

    db = Engine(d, mode="mvcc")
    db.execute("CREATE TABLE acct (id INT PRIMARY KEY, bal INT)")
    db.execute("INSERT INTO acct VALUES (1,100),(2,200)")     # committed

    t = db.begin_transaction()
    db.execute("INSERT INTO acct VALUES (3,999)", txn=t)       # uncommitted
    db.execute("UPDATE acct SET bal=5 WHERE id=1", txn=t)      # uncommitted
    # Simulate STEAL then crash: dirty pages + WAL reach disk, no commit/close.
    db.wal.flush()
    db.bp.flush_all()
    del db

    db2 = Engine(d, mode="mvcc")
    r = db2.execute("SELECT id, bal FROM acct ORDER BY id")
    assert r.rows == [(1, 100), (2, 200)]
    db2.close()


def test_clean_restart_durability(tmp_path):
    d = str(tmp_path / "db")
    db = Engine(d, mode="mvcc")
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    db.execute("INSERT INTO t VALUES (1,1),(2,2)")
    db.close()

    db2 = Engine(d, mode="mvcc")
    r = db2.execute("SELECT id, v FROM t ORDER BY id")
    assert r.rows == [(1, 1), (2, 2)]
    # recovery is idempotent: a second restart is still correct
    db2.close()
    db3 = Engine(d, mode="mvcc")
    r = db3.execute("SELECT COUNT(*) FROM t")
    assert r.rows == [(2,)]
    db3.close()
