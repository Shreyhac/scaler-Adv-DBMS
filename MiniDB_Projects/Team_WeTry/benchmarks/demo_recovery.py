"""Narrated crash-recovery demonstration (useful for the live demo / viva).

Phase 1: commit two rows, then start a transaction that inserts and updates more
         rows but never commits. We flush dirty pages + WAL to disk (the STEAL
         policy) and then drop the engine WITHOUT committing or checkpointing --
         i.e. we simulate a hard crash.
Phase 2: reopen the database. Startup runs ARIES-style recovery, which redoes
         committed work and undoes the in-flight transaction. We print the state
         before and after to show committed data survived and uncommitted data
         vanished.
"""

import _bootstrap  # noqa: F401
import shutil
import tempfile

from minidb.engine import Engine


def show(db, label):
    r = db.execute("SELECT id, bal FROM acct ORDER BY id")
    print(f"  {label}: {r.rows}")


def main():
    d = tempfile.mkdtemp(prefix="demo_recovery_")
    try:
        print("Phase 1 — write committed data, then crash mid-transaction")
        db = Engine(d, mode="mvcc")
        db.execute("CREATE TABLE acct (id INT PRIMARY KEY, bal INT)")
        db.execute("INSERT INTO acct VALUES (1,100),(2,200)")   # committed
        show(db, "committed state")

        t = db.begin_transaction()
        db.execute("INSERT INTO acct VALUES (3,999)", txn=t)     # uncommitted
        db.execute("UPDATE acct SET bal=0 WHERE id=1", txn=t)    # uncommitted
        print("  in-flight txn wrote id=3 and set id=1 bal=0 (NOT committed)")

        db.wal.flush()        # WAL durable (write-ahead)
        db.bp.flush_all()     # dirty pages stolen to disk
        print("  *** simulating crash (no COMMIT, no checkpoint) ***")
        del db                # abandon the engine

        print("\nPhase 2 — reopen; recovery runs automatically")
        db2 = Engine(d, mode="mvcc")
        show(db2, "after recovery")
        ok = db2.execute("SELECT id, bal FROM acct ORDER BY id").rows == [(1, 100), (2, 200)]
        print(f"\n  committed rows preserved, uncommitted rolled back: "
              f"{'PASS' if ok else 'FAIL'}")
        db2.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
