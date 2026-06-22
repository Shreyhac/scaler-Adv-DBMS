"""Extension-track benchmark: MVCC vs 2PL under read/write contention.

Workload: a fixed set of rows, a few writer threads that hold an exclusive lock
on a hot row for a short "think time" inside an explicit transaction, and many
reader threads doing point lookups on random rows for a fixed duration.

What we expect (and measure):
* 2PL  -- a reader whose row is being written must wait until the writer
  commits and releases its lock, so reader throughput drops and read latency
  spikes under contention.
* MVCC  -- readers never take locks; they read the last version visible to
  their snapshot and never block on writers, sustaining read throughput.

Caveat (honest): CPython's GIL serialises CPU work, so this measures
*blocking / latency* behaviour, not multi-core parallel speedup. The effect we
isolate is lock-induced waiting, which is precisely the MVCC-vs-2PL difference.
"""

import _bootstrap  # noqa: F401
import random
import shutil
import statistics
import tempfile
import threading
import time

from minidb.engine import Engine
from minidb.txn.lock_manager import DeadlockError
from minidb.engine import WriteConflictError

N_ROWS = 1000
N_READERS = 8
N_WRITERS = 2
DURATION = 2.0          # seconds
WRITER_THINK = 0.002    # seconds a writer holds its lock per txn
HOT_KEYS = 50           # writers and readers contend on this hot range


def build(mode):
    d = tempfile.mkdtemp(prefix=f"bench_{mode}_")
    db = Engine(d, mode=mode)
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    # batch the load into a few multi-row inserts
    vals = ",".join(f"({i},{i})" for i in range(N_ROWS))
    db.execute(f"INSERT INTO t VALUES {vals}")
    return db, d


def run(mode):
    db, d = build(mode)
    stop = threading.Event()
    read_count = [0]
    write_count = [0]
    read_latencies = []
    lat_lock = threading.Lock()

    def reader():
        local = 0
        lats = []
        while not stop.is_set():
            k = random.randint(0, HOT_KEYS - 1)
            t0 = time.perf_counter()
            try:
                db.execute(f"SELECT v FROM t WHERE id={k}")
                lats.append(time.perf_counter() - t0)
                local += 1
            except (DeadlockError, WriteConflictError):
                pass
        with lat_lock:
            read_count[0] += local
            read_latencies.extend(lats)

    def writer():
        local = 0
        while not stop.is_set():
            k = random.randint(0, HOT_KEYS - 1)
            txn = db.begin_transaction()
            try:
                db.execute(f"UPDATE t SET v={random.randint(0, 1 << 30)} "
                           f"WHERE id={k}", txn=txn)
                time.sleep(WRITER_THINK)        # hold the lock briefly
                db.commit(txn)
                local += 1
            except (DeadlockError, WriteConflictError):
                db.abort(txn)
        write_count[0] += local

    threads = [threading.Thread(target=reader) for _ in range(N_READERS)]
    threads += [threading.Thread(target=writer) for _ in range(N_WRITERS)]
    for t in threads:
        t.start()
    time.sleep(DURATION)
    stop.set()
    for t in threads:
        t.join()
    db.close()
    shutil.rmtree(d, ignore_errors=True)

    reads = read_count[0]
    p50 = statistics.median(read_latencies) * 1e3 if read_latencies else 0
    p99 = (sorted(read_latencies)[int(len(read_latencies) * 0.99)] * 1e3
           if read_latencies else 0)
    return {
        "mode": mode,
        "reads_per_sec": reads / DURATION,
        "writes_per_sec": write_count[0] / DURATION,
        "read_p50_ms": p50,
        "read_p99_ms": p99,
    }


def main():
    print(f"workload: {N_ROWS} rows, {N_READERS} readers, {N_WRITERS} writers, "
          f"{DURATION}s, hot keys={HOT_KEYS}, writer think={WRITER_THINK*1e3:.0f}ms\n")
    results = [run("2pl"), run("mvcc")]
    hdr = f"{'mode':6} {'reads/s':>12} {'writes/s':>10} {'read p50 ms':>12} {'read p99 ms':>12}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        print(f"{r['mode']:6} {r['reads_per_sec']:12.0f} {r['writes_per_sec']:10.0f} "
              f"{r['read_p50_ms']:12.3f} {r['read_p99_ms']:12.3f}")
    mvcc = next(r for r in results if r["mode"] == "mvcc")
    tpl = next(r for r in results if r["mode"] == "2pl")
    if tpl["reads_per_sec"]:
        ratio = mvcc["reads_per_sec"] / tpl["reads_per_sec"]
        print(f"\nMVCC read throughput vs 2PL: {ratio:.2f}x")
        print(f"MVCC tail latency (p99) vs 2PL: "
              f"{tpl['read_p99_ms'] / mvcc['read_p99_ms']:.2f}x lower"
              if mvcc["read_p99_ms"] else "")


if __name__ == "__main__":
    main()
