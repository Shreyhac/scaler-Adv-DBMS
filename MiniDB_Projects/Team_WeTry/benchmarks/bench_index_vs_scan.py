"""Optimizer benchmark: index scan vs sequential scan for point lookups.

We load N rows and run many equality point lookups two ways:
* on the primary key  -> the optimizer chooses an IndexScan  (O(log N) probe)
* on a non-indexed col -> the optimizer must SeqScan          (O(N) scan)

As N grows, index-lookup latency stays roughly flat while seq-scan latency grows
linearly -- the reason a cost-based optimizer prefers the index when a usable
equality predicate exists.
"""

import _bootstrap  # noqa: F401
import random
import shutil
import tempfile
import time

from minidb.engine import Engine

SIZES = [1000, 5000, 20000]
LOOKUPS = 300


def load(n):
    d = tempfile.mkdtemp(prefix="bench_idx_")
    db = Engine(d, mode="mvcc")
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    batch = 1000
    for start in range(0, n, batch):
        vals = ",".join(f"({i},{i})" for i in range(start, min(start + batch, n)))
        db.execute(f"INSERT INTO t VALUES {vals}")
    return db, d


def time_lookups(db, column, n):
    keys = [random.randint(0, n - 1) for _ in range(LOOKUPS)]
    t0 = time.perf_counter()
    for k in keys:
        db.execute(f"SELECT v FROM t WHERE {column}={k}")
    return (time.perf_counter() - t0) / LOOKUPS * 1e3   # ms/lookup


def main():
    print(f"{LOOKUPS} point lookups each; latency in ms/lookup\n")
    hdr = f"{'rows':>8} {'index (id)':>14} {'seqscan (v)':>14} {'speedup':>10}"
    print(hdr)
    print("-" * len(hdr))
    for n in SIZES:
        db, d = load(n)
        # confirm the optimizer actually chooses each path
        assert "IndexScan" in db.explain("SELECT v FROM t WHERE id=1")
        assert "SeqScan" in db.explain("SELECT v FROM t WHERE v=1")
        idx_ms = time_lookups(db, "id", n)
        seq_ms = time_lookups(db, "v", n)
        print(f"{n:>8} {idx_ms:>14.4f} {seq_ms:>14.4f} {seq_ms / idx_ms:>9.1f}x")
        db.close()
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
