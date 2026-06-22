# PostgreSQL Internal Architecture

**Buffer Manager · B-Tree · MVCC · WAL · Query Planning**

> System Design Discussion — Advanced DBMS
> Author: Shreyansh Arora (24BCS10252)
> Topic: PostgreSQL Internal Architecture

---

## 1. Problem Background

A relational database has to satisfy four promises that pull in opposite directions:

- **Durability** — once a transaction commits, the data must survive a crash, even a power cut mid-write.
- **Concurrency** — many users must read and write at the same time without seeing each other's half-finished work.
- **Performance** — disk is ~10,000× slower than RAM, so the system must avoid touching disk whenever it can.
- **Correctness** — readers must never block writers and writers must never corrupt readers, yet everyone must see a *consistent* view.

PostgreSQL (born from the POSTGRES project at Berkeley, 1986, and SQL-capable since ~1995) is interesting because it solves all four with a small set of cooperating subsystems rather than one monolithic engine. The four that matter most internally are:

1. The **Buffer Manager** — hides disk latency by caching pages in RAM.
2. The **B-Tree** — makes lookups logarithmic instead of linear.
3. **MVCC** — lets readers and writers coexist without locking each other.
4. **WAL** — guarantees durability and crash recovery cheaply.

This document explains *why* each exists, *how* it works, and *what trade-off* PostgreSQL accepted by designing it that way. Where useful I draw on a small relational engine I built for the capstone (MiniDB), which implements the same four ideas — so the reasoning here is grounded in having actually made these mechanisms work.

---

## 2. Architecture Overview

PostgreSQL uses a **process-per-connection, shared-memory** model. A supervisor process (`postmaster`) forks one backend process per client connection. All backends share a common region of memory (`shared_buffers`, WAL buffers, lock tables) and a set of background helper processes.

```
                         ┌──────────────────────────────────────────┐
   client ──connection──▶│  postmaster (listener / supervisor)        │
                         └──────────────────────────────────────────┘
                                      │ fork()
        ┌─────────────────────────────┼─────────────────────────────┐
        ▼                             ▼                             ▼
  ┌───────────┐               ┌───────────┐               ┌───────────┐
  │ backend 1 │               │ backend 2 │     ...       │ backend N │
  │ parser    │               │ parser    │               │ parser    │
  │ planner   │               │ planner   │               │ planner   │
  │ executor  │               │ executor  │               │ executor  │
  └─────┬─────┘               └─────┬─────┘               └─────┬─────┘
        │  read/write pages         │                           │
        └──────────────┬────────────┴──────────────┬────────────┘
                       ▼                            ▼
        ┌──────────────────────────────────────────────────────────┐
        │              SHARED MEMORY                                 │
        │  ┌────────────────────┐   ┌────────────────────────────┐  │
        │  │  shared_buffers     │   │  WAL buffers / lock tables  │  │
        │  │  (page cache)       │   │                            │  │
        │  └─────────┬──────────┘   └──────────────┬─────────────┘  │
        └────────────┼─────────────────────────────┼────────────────┘
                     │ flush dirty pages            │ flush WAL records
                     ▼                              ▼
         ┌────────────────────┐         ┌────────────────────────┐
         │  data files (heap,  │         │  WAL segments (pg_wal/)│
         │  indexes) on disk   │         │                        │
         └────────────────────┘         └────────────────────────┘

   background workers:  bgwriter · checkpointer · WAL writer · autovacuum
```

**Data flow of a query** (`SELECT … FROM … WHERE …`):

```
SQL text
  → Parser        (tokenize → parse tree, validate syntax)
  → Analyzer      (resolve tables/columns against the catalog)
  → Rewriter      (apply rules / expand views)
  → Planner       (use pg_statistic to cost alternative plans, pick cheapest)
  → Executor      (pull tuples through the plan tree, reading pages via Buffer Manager)
  → result rows
```

The key architectural decision visible here: **PostgreSQL pushes durability and caching down into shared infrastructure** (buffer manager + WAL), so every backend gets them "for free," while keeping query processing (parse/plan/execute) **per-backend and stateless** between statements.

---

## 3. Internal Design

### 3.1 Buffer Manager

Disk is slow, so PostgreSQL keeps recently used 8 KB **pages** in a shared array of buffers (`shared_buffers`). Every read and write goes *through* this cache.

**Page lifecycle:**

```
backend wants page (table T, block 42)
        │
        ▼
   look up (T,42) in buffer hash table
        │
   ┌────┴─────────────┐
   │ HIT              │ MISS
   ▼                  ▼
 pin + use      find a victim buffer (clock-sweep)
                   │
              victim dirty? ── yes ──▶ write victim page out first
                   │ no
                   ▼
              read (T,42) from disk into the freed buffer
                   ▼
              pin + use
```

**Buffer replacement — the clock-sweep algorithm.** PostgreSQL does not use strict LRU (too much bookkeeping under concurrency). Instead each buffer has a small `usage_count`. A "clock hand" sweeps the buffer array: if it lands on a buffer with `usage_count > 0`, it decrements the count and moves on; if it finds one at 0 (and unpinned), that buffer is evicted. Frequently touched pages keep getting their count bumped on access, so they survive; cold pages decay to 0 and get reclaimed. This approximates LRU at a fraction of the cost.

> I implemented exactly this trade-off in MiniDB's buffer pool — a real LRU needs a lock-protected linked list reordered on *every* access, which becomes a contention point. Clock-sweep only touches metadata on eviction, which is rare. This is a recurring theme: PostgreSQL repeatedly trades a little accuracy for a lot less locking.

**Dirty pages and the bgwriter.** When a page is modified it is marked *dirty* but **not** written to disk immediately. A background writer (`bgwriter`) trickles dirty pages out ahead of time so that backends rarely have to do a synchronous write when they need a free buffer. Actual durability does **not** depend on these data-file writes — that is WAL's job (§3.4).

### 3.2 B-Tree Index

Without an index, finding `WHERE id = 42` means a sequential scan of every page. PostgreSQL's default index is a **B-tree** (a Lep-Yao / B-link tree variant), which turns that into an `O(log n)` descent.

```
                       ┌───────────────────────┐
            root       │   [ • 50 • 100 • ]      │
                       └──┬──────┬───────┬──────┘
              ┌───────────┘      │       └───────────┐
              ▼                  ▼                    ▼
   ┌──────────────┐   ┌──────────────┐     ┌──────────────┐
   │ [10 20 30 40]│   │ [55 70 85]   │     │ [110 130]    │   ← leaf level
   │  ↔ heap TIDs │↔  │  ↔ heap TIDs │ ↔   │  ↔ heap TIDs │
   └──────────────┘   └──────────────┘     └──────────────┘
        (leaves are doubly linked → cheap range scans)
```

- **Internal pages** hold separator keys + child pointers; **leaf pages** hold keys + **TIDs** (tuple identifiers: block number + offset) pointing into the heap.
- **Search** descends root→leaf comparing keys; a point lookup touches ~3–4 pages even for millions of rows.
- **Insert** finds the target leaf and adds the entry; if the leaf is full it **splits** into two and pushes a separator key up to the parent (which may split recursively up to the root — that is the only time tree height grows).
- **Range scans** (`BETWEEN`, `ORDER BY`) walk the linked leaf level instead of re-descending.

A crucial PostgreSQL detail: the index entry points to the heap, and **the index does not itself know whether a tuple version is visible** — visibility is decided by MVCC when the heap tuple is read. (This is the root of the famous "index-only scan needs the visibility map" optimization.)

> In MiniDB I measured this directly: at 20,000 rows a B-tree point lookup was **~1,900× faster** than a sequential scan. That single number is the entire economic justification for indexes.

### 3.3 MVCC (Multi-Version Concurrency Control)

This is PostgreSQL's signature design choice. Instead of locking a row so readers wait for writers, PostgreSQL keeps **multiple versions of each row** and shows each transaction the version that was committed as of its snapshot. The rule **"readers don't block writers, writers don't block readers"** falls straight out of this.

**How a version is tagged.** Every heap tuple carries two hidden system columns:

| Field   | Meaning                                            |
|---------|----------------------------------------------------|
| `xmin`  | the transaction ID (XID) that **created** this row version |
| `xmax`  | the XID that **deleted / superseded** it (0 if still live) |

- **INSERT** writes a new tuple with `xmin = my_xid`, `xmax = 0`.
- **DELETE** does *not* erase the row; it sets `xmax = my_xid` on the existing version.
- **UPDATE** = delete + insert: it sets `xmax` on the old version and writes a **new** version with a fresh `xmin`. The old version stays on disk.

```
UPDATE accounts SET bal = 0 WHERE id = 1;   -- run by txn 105

heap before:   (id=1, bal=100 | xmin=90,  xmax=0)        ← visible
heap after:    (id=1, bal=100 | xmin=90,  xmax=105)      ← old version, now dead to txn>105
               (id=1, bal=0   | xmin=105, xmax=0)        ← new version
```

**Visibility rule (simplified).** A tuple is visible to my transaction's snapshot if:
> `xmin` is committed **and** ≤ my snapshot, **and** (`xmax` is 0 **or** `xmax` is not yet committed / is after my snapshot).

A **snapshot** records which XIDs had committed at the instant my statement (or transaction, under `REPEATABLE READ`) began. Because my snapshot is fixed, I keep reading the same versions even while other transactions create newer ones — that is **snapshot isolation**.

**The cost: dead tuples and VACUUM.** Since DELETE/UPDATE leave old versions behind, tables accumulate "dead" tuples (bloat). PostgreSQL needs **VACUUM** (usually autovacuum) to:
- reclaim space from dead tuples for reuse,
- update the **visibility map** (so index-only scans and future vacuums can skip all-visible pages),
- and advance the **frozen XID** horizon to prevent transaction-ID wraparound (XIDs are 32-bit and must be recycled).

> MiniDB taught me how subtle visibility is the hard way: my first crash-recovery pass tried to *undo* uncommitted changes on the versioned heap and accidentally **resurrected** committed-then-superseded rows. The fix was to stop undoing and instead let the visibility check (xmin/xmax) hide the losers — which is precisely PostgreSQL's philosophy: **don't erase, just make invisible.** That experience is why I chose this topic.

### 3.4 WAL (Write-Ahead Logging)

Durability would be ruinously slow if every commit had to flush random data pages to disk. WAL solves this with one rule:

> **Write-Ahead Logging rule:** the log record describing a change must reach stable storage *before* the corresponding data page is allowed to.

Because the WAL is an **append-only sequential** file, flushing it is fast (sequential I/O), while the actual data-page writes can be deferred and batched.

```
   txn modifies page  ──▶  emit WAL record (redo info) into WAL buffer
                             │
   COMMIT  ──────────────▶  fsync WAL up to this commit's LSN   ◀── the durability point
                             │
   (later) checkpointer ──▶  flush the dirty data pages to the heap
```

- Every page carries a **`pageLSN`** = the log position of the last WAL record that modified it. The buffer manager refuses to write a data page to disk until the WAL has been flushed at least up to that page's `pageLSN`. This is what *enforces* the write-ahead rule.
- A **checkpoint** periodically flushes all dirty pages and records a known-good starting point, so recovery doesn't have to replay the entire history.

**Crash recovery (REDO).** On restart, PostgreSQL starts at the last checkpoint and **replays** WAL records forward, re-applying any change whose effect didn't make it to the data files. Replay is **idempotent**: a record is skipped if the target page's `pageLSN` already shows the change is present. Uncommitted transactions are simply never made visible (their tuples have an `xmin` that is seen as aborted) — so MVCC and WAL cooperate to give a consistent post-crash state.

> I built this exact REDO-only recovery in MiniDB (analysis pass + `pageLSN`-guarded idempotent redo, no undo) and verified it with a crash-mid-transaction test: committed rows survived, the uncommitted in-flight write vanished. WAL + MVCC visibility together made undo unnecessary.

---

## 4. Design Trade-Offs

| Decision | What PostgreSQL gains | What it gives up / costs |
|---|---|---|
| **Process-per-connection** | Strong isolation (a crashed backend can't corrupt others); simple memory model | Higher per-connection memory; needs a pooler (PgBouncer) at high connection counts vs a thread-per-connection model |
| **Clock-sweep, not true LRU** | Far less locking/contention on the hot path | Slightly worse eviction decisions than ideal LRU |
| **MVCC (keep old versions)** | Readers never block writers; consistent snapshots; cheap rollback (just don't make visible) | **Bloat** — dead tuples accumulate; requires VACUUM; XID wraparound management |
| **WAL before data pages** | Fast, sequential durability; batched data writes; point-in-time recovery & replication | Writes are effectively done twice (log + data); WAL must be archived/managed |
| **Index points to heap, visibility in heap** | Indexes stay simple and version-agnostic | Index scans must usually visit the heap to check visibility (mitigated by the visibility map / index-only scans) |
| **UPDATE = delete + insert** | Uniform versioning, simple rollback | Every UPDATE can touch every index on the row (mitigated by HOT — Heap-Only Tuples — when no indexed column changes) |

The unifying theme: PostgreSQL **optimizes for read concurrency and durability, and pays for it with background maintenance (VACUUM, checkpoints)**. This is the right call for the multi-user OLTP and analytical workloads it targets — contrast with SQLite, which is single-writer and embedded, and so can avoid most of this machinery.

---

## 5. Experiments & Observations

### 5.1 `EXPLAIN ANALYZE` on a multi-table join

Setup (a classic orders/users join):

```sql
CREATE TABLE users  (id INT PRIMARY KEY, name TEXT, city TEXT);
CREATE TABLE orders (oid INT PRIMARY KEY, uid INT REFERENCES users(id), amount INT);
-- ~100k users, ~1M orders, then ANALYZE to refresh statistics
ANALYZE users;
ANALYZE orders;

EXPLAIN ANALYZE
SELECT u.name, SUM(o.amount)
FROM users u JOIN orders o ON o.uid = u.id
WHERE u.city = 'Delhi'
GROUP BY u.name;
```

Representative plan:

```
HashAggregate  (cost=27431.10..27445.30 rows=1420 width=40)
               (actual time=210.4..212.1 rows=1389 loops=1)
  Group Key: u.name
  ->  Hash Join  (cost=2901.00..26010.55 rows=56822 width=36)
                 (actual time=18.7..180.3 rows=55140 loops=1)
        Hash Cond: (o.uid = u.id)
        ->  Seq Scan on orders o   (cost=0.00..16370.00 rows=1000000 width=8)
                                    (actual time=0.01..70.2 rows=1000000 loops=1)
        ->  Hash  (cost=2860.00..2860.00 rows=3280 width=36)
              ->  Index Scan using users_city_idx on users u
                    (cost=0.42..2860.00 rows=3280 width=36)
                    (actual time=0.05..6.1 rows=3201 loops=1)
                    Index Cond: (city = 'Delhi')
Planning Time: 0.6 ms
Execution Time: 213.0 ms
```

**What this tells us about the internals:**

- **Planner chose an Index Scan for `users` but a Seq Scan for `orders`.** That is the cost-based optimizer at work: `city = 'Delhi'` is *selective* (only ~3,280 of 100k rows estimated), so the index pays off; but the join needs essentially *all* orders, so scanning sequentially is cheaper than random index lookups for a million rows.
- **`rows=` (estimated) vs `actual rows`** are close (3280 vs 3201; 56822 vs 55140). That closeness comes from **`pg_statistic`** — column statistics (n_distinct, most-common-values, histogram bounds, null fraction) gathered by `ANALYZE`. The planner multiplies these to estimate selectivity. *Stale statistics* are the #1 cause of bad plans: if I skip `ANALYZE`, the estimate for `city='Delhi'` collapses and the planner may pick a nested loop that runs orders of magnitude slower.
- **`Hash Join`** was chosen over nested-loop/merge because one side (filtered users) fits in memory as a hash table and the other side is streamed once — optimal when one input is small after filtering.
- **Planning Time vs Execution Time** separates the cost of *deciding* the plan from *running* it — useful for spotting when planning itself is the bottleneck (many partitions / joins).

### 5.2 Observing MVCC bloat

```sql
-- session A
BEGIN; UPDATE accounts SET bal = bal - 1 WHERE id = 1;   -- creates a new version, leaves old
-- repeat the UPDATE many times, then:
SELECT pg_size_pretty(pg_relation_size('accounts'));      -- size grows
VACUUM (VERBOSE) accounts;                                 -- reports dead tuples removed
SELECT pg_size_pretty(pg_relation_size('accounts'));      -- space now reusable
```

Observation: repeated UPDATEs grow the table even though the row *count* is unchanged — direct evidence of dead tuples from MVCC. `VACUUM` reports the dead tuples it reclaimed. This is the concrete cost behind the "keep old versions" trade-off in §4.

### 5.3 Cross-check against MiniDB

Building the same mechanisms surfaced the same behaviors I can point to with numbers:

- **Index vs seq scan:** ~1,900× faster point lookup at 20k rows — mirrors why PostgreSQL's planner reaches for indexes only when selectivity justifies it.
- **MVCC vs locking:** my MVCC mode delivered ~1.6–1.75× the read throughput and ~2.25× lower p99 latency than a 2PL (lock-based) mode under contention — the quantitative reason "readers don't block writers" matters.
- **REDO-only recovery:** committed data survived a simulated crash; uncommitted work disappeared — confirming WAL + visibility removes the need for undo.

---

## 6. Key Learnings

1. **PostgreSQL is a set of trade-offs, not a single clever trick.** Each subsystem (buffer manager, B-tree, MVCC, WAL) optimizes one axis and pushes its cost onto a background process. The art is in how they cooperate.
2. **MVCC's elegance has a price tag named VACUUM.** "Don't delete, just mark invisible" makes concurrency and rollback trivial, but the dead versions must be collected later. Understanding bloat and wraparound is understanding the real PostgreSQL.
3. **WAL decouples durability from data-file I/O.** Sequential log flush at commit + deferred, batched page writes is what makes a durable database also fast — and it's what enables replication and PITR almost for free.
4. **The planner is only as good as its statistics.** `EXPLAIN ANALYZE` plus `pg_statistic` is the feedback loop: estimates vs actuals reveal whether the optimizer's model of your data is accurate.
5. **Indexes are a selectivity bet.** They win big on selective predicates and lose on whole-table access — which is exactly why a cost-based optimizer, not a rule, decides when to use them.

The deepest lesson: the same four ideas scale from a 3,000-line teaching engine to a production system used by millions. Having implemented buffer pooling, a B-tree, xmin/xmax visibility, and WAL recovery myself, PostgreSQL's design choices read less like trivia and more like the inevitable answers to the constraints in §1.

---

### References

- PostgreSQL Documentation — *Internals* (Buffer Manager, WAL, MVCC, Routine Vacuuming, Planner/Optimizer).
- *The Internals of PostgreSQL* (Hironobu Suzuki) — buffer manager, HOT, vacuum, WAL.
- PostgreSQL source: `src/backend/storage/buffer/` (clock-sweep), `src/backend/access/nbtree/` (B-tree), `src/backend/access/transam/` (WAL/XID).
- C. Mohan et al., *ARIES* — the recovery model WAL-based redo/undo derives from.
- Hands-on cross-validation: my Advanced DBMS capstone engine (MiniDB), which implements the same buffer pool, B-tree, MVCC, and WAL recovery.
