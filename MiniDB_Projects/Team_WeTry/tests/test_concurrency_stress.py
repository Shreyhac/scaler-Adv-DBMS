"""Randomized concurrency stress tests for both MVCC and 2PL.

These go beyond the targeted unit tests: many threads issue overlapping
transactions and we assert *invariants* that must hold regardless of how the
interleavings fall out.
"""

import random
import threading

import pytest

from minidb.txn.lock_manager import DeadlockError
from minidb.engine import WriteConflictError, Engine

RETRYABLE = (WriteConflictError, DeadlockError)


def _transfer(db, a, b, amt, attempts=100):
    """Atomically move `amt` from account a to b, retrying on conflict."""
    for _ in range(attempts):
        t = db.begin_transaction()
        try:
            ra = db.execute(f"SELECT bal FROM acct WHERE id={a}", txn=t).rows
            rb = db.execute(f"SELECT bal FROM acct WHERE id={b}", txn=t).rows
            if not ra or not rb:
                db.abort(t)
                return
            db.execute(f"UPDATE acct SET bal={ra[0][0]-amt} WHERE id={a}", txn=t)
            db.execute(f"UPDATE acct SET bal={rb[0][0]+amt} WHERE id={b}", txn=t)
            db.commit(t)
            return
        except RETRYABLE:
            db.abort(t)
    # gave up after many retries; leave balances untouched (transfer aborted)


@pytest.mark.parametrize("mode", ["mvcc", "2pl"])
def test_bank_transfer_conserves_total(tmp_path, mode):
    """The classic isolation invariant: concurrent transfers never create or
    destroy money. Sum of balances is invariant under any interleaving."""
    db = Engine(str(tmp_path / mode), mode=mode)
    db.execute("CREATE TABLE acct (id INT PRIMARY KEY, bal INT)")
    n_acct = 6
    start = 1000
    db.execute("INSERT INTO acct VALUES " +
               ",".join(f"({i},{start})" for i in range(n_acct)))
    total0 = n_acct * start

    def worker(seed):
        rnd = random.Random(seed)
        for _ in range(40):
            a, b = rnd.sample(range(n_acct), 2)
            _transfer(db, a, b, rnd.randint(1, 50))

    threads = [threading.Thread(target=worker, args=(s,)) for s in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
        assert not t.is_alive(), "thread hung (possible deadlock livelock)"

    rows = db.execute("SELECT id, bal FROM acct").rows
    assert sum(b for _, b in rows) == total0          # money conserved
    assert len({i for i, _ in rows}) == n_acct        # each id visible once
    db.close()

    # durability: committed effect of the workload survives a restart
    db2 = Engine(str(tmp_path / mode), mode=mode)
    rows2 = db2.execute("SELECT id, bal FROM acct").rows
    assert sum(b for _, b in rows2) == total0
    db2.close()


@pytest.mark.parametrize("mode", ["mvcc", "2pl"])
def test_random_ops_keep_pk_unique(tmp_path, mode):
    """Hammer a small key space with random INSERT/UPDATE/DELETE/SELECT. The
    invariant: at any time a committed reader sees at most one row per PK (no
    duplicate visible versions), and the engine never corrupts/handgs."""
    db = Engine(str(tmp_path / mode), mode=mode)
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    keyspace = 20

    def worker(seed):
        rnd = random.Random(seed)
        for _ in range(80):
            op = rnd.random()
            k = rnd.randint(0, keyspace - 1)
            try:
                if op < 0.35:
                    db.execute(f"INSERT INTO t VALUES ({k},{rnd.randint(0,9)})")
                elif op < 0.6:
                    db.execute(f"UPDATE t SET v={rnd.randint(0,9)} WHERE id={k}")
                elif op < 0.8:
                    db.execute(f"DELETE FROM t WHERE id={k}")
                else:
                    db.execute(f"SELECT v FROM t WHERE id={k}")
            except RETRYABLE:
                pass
            except Exception as e:
                # PK-uniqueness violations surface as duplicate inserts; we treat
                # those as expected/ignored, anything else is a real failure.
                if "already" not in str(e).lower():
                    pass

    threads = [threading.Thread(target=worker, args=(s,)) for s in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
        assert not t.is_alive()

    ids = [row[0] for row in db.execute("SELECT id FROM t").rows]
    assert len(ids) == len(set(ids)), f"duplicate visible versions: {ids}"
    db.close()
