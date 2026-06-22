# MiniDB — A Working Relational Database Engine

> Advanced DBMS Capstone Project · Extension Track **B (MVCC)**

MiniDB is a small but complete relational database engine built from
foundational components: a page-based storage engine with a buffer pool, a
B+ tree index, a SQL parser, a cost-based optimizer, a volcano-model execution
engine, ACID transactions with Write-Ahead Logging and crash recovery, and two
interchangeable concurrency-control schemes — **strict Two-Phase Locking (the
core requirement)** and **Multi-Version Concurrency Control (the extension)** —
sharing one on-disk format so they can be benchmarked head-to-head.

It is ~3,100 lines of dependency-free Python (only `pytest` for tests) and runs
real SQL end-to-end through an interactive shell.

```sql
minidb> CREATE TABLE emp (id INT PRIMARY KEY, name TEXT, dept TEXT, sal INT);
minidb> INSERT INTO emp VALUES (1,'ana','eng',120),(2,'ben','eng',90);
minidb> SELECT dept, COUNT(*) AS n, AVG(sal) AS avg_sal FROM emp GROUP BY dept;
dept | n | avg_sal
-----+---+--------
eng  | 2 | 105.0
(1 row)
```

---

## Team

> **Team name:** `WeTry`
>
> | Full Name | Roll Number | Scaler Email |
> |-----------|-------------|--------------|
> | Shreyansh Arora | 10252 | shreyansh.24bcs10252@sst.scaler.com |
> | Lavya Tanotra   | 10124 | lavya.24bcs10124@sst.scaler.com |
>
> **PR title to use:** `TEAM_WeTry`

---

## 1. Project Overview

### Problem statement
Production databases are dense bundles of engineering trade-offs — storage
layout, indexing, query planning, concurrency, durability — that are hard to
appreciate from the outside. The goal of MiniDB is to **build those components
ourselves and integrate them into one coherent system**, so each design
decision is something we made and can defend.

### Goals
- Implement every required subsystem: storage engine, B+ tree index, SQL
  execution (`SELECT`/`JOIN`/`INSERT`/`UPDATE`/`DELETE`), cost-based optimizer,
  serializable transactions via 2PL, and WAL-based crash recovery.
- Keep the architecture **modular and explainable** rather than feature-rich.
- Implement one extension track to depth.

### Chosen extension track — **B: Concurrency (MVCC)**
We replace the lock-based reader/writer coordination of 2PL with
**Multi-Version Concurrency Control**: writers create new row versions instead
of overwriting, and readers see a consistent **snapshot** without taking any
locks. Because both schemes run on the *same versioned heap*, switching is a one
-line mode flag and the MVCC-vs-2PL benchmark is a true apples-to-apples
comparison (see §9, §10).

---

## 2. System Architecture

```
                         ┌──────────────────────────┐
        SQL text  ─────▶ │  Parser (lexer → AST)     │
                         └────────────┬─────────────┘
                                      │ AST
                         ┌────────────▼─────────────┐    table_stats()
                         │  Cost-Based Optimizer     │◀───────────────┐
                         │  (access path, join order)│                │
                         └────────────┬─────────────┘                │
                                      │ operator tree                │
                         ┌────────────▼─────────────┐                │
                         │  Execution Engine         │                │
                         │  (volcano operators)      │                │
                         └───┬───────────────┬───────┘                │
              visibility /   │               │  versioned reads/writes│
              locking via    │               │                        │
            ┌────────────────▼───┐    ┌──────▼──────────┐    ┌────────┴───────┐
            │ Concurrency Control│    │  Table Heap     │    │   Catalog      │
            │  ┌───────┐ ┌─────┐ │    │ (versioned rows)│    │ (schemas/PK)   │
            │  │  2PL  │ │MVCC │ │    └──────┬──────────┘    └────────────────┘
            │  │ locks │ │snap │ │           │
            │  └───────┘ └─────┘ │    ┌──────▼──────────┐    ┌────────────────┐
            └────────┬───────────┘    │  B+ Tree Index  │    │ Write-Ahead Log│
                     │                │ (key → RIDs)    │    │  (durability)  │
       deadlock detect│                └──────┬──────────┘    └──────┬─────────┘
                                              │                      │
                                       ┌──────▼──────────────────────▼──────┐
                                       │           Buffer Pool               │
                                       │   (LRU, pin/dirty, WAL-before-page)  │
                                       └──────────────────┬──────────────────┘
                                                          │ pages
                                       ┌──────────────────▼──────────────────┐
                                       │            Disk Manager              │
                                       │   minidb.data  (page array)          │
                                       └──────────────────────────────────────┘
```

### Major modules (`src/minidb/`)
| Module | Responsibility |
|--------|----------------|
| `storage/page.py` | Slotted page layout (records + slot directory + page LSN) |
| `storage/disk_manager.py` | Page ↔ file I/O, allocation |
| `storage/buffer_pool.py` | In-memory page cache, LRU eviction, WAL-before-page rule |
| `storage/table_heap.py` | A table's records spread across pages |
| `record/` | Schema, typed tuple (de)serialization, version header, RID |
| `index/btree.py` | B+ tree (key → list of RIDs) |
| `catalog/catalog.py` | Table/index metadata |
| `sql/` | Lexer, recursive-descent parser, AST |
| `optimizer/optimizer.py` | Selectivity, access-path & join planning |
| `execution/` | Volcano operators, expression eval, exec context |
| `txn/` | Transaction manager, 2PL lock manager, MVCC visibility |
| `recovery/` | WAL records + ARIES-style recovery |
| `engine.py` | Facade tying it together |
| `cli.py` | Interactive REPL |

### Data flow (a `SELECT`)
1. **Parse** SQL → AST.
2. **Optimize**: split predicates, estimate cardinalities from `table_stats`,
   choose IndexScan vs SeqScan and join algorithm/order → operator tree.
3. **Execute**: operators pull rows on demand; each row read passes through the
   **execution context**, which applies MVCC visibility (and, in 2PL mode,
   takes shared locks).
4. **Post-process**: aggregation / `ORDER BY` / `LIMIT` / projection.
5. Rows are returned to the caller (or printed by the CLI).

---

## 3. Storage Layer

### Page format — slotted pages
Every page is a fixed `PAGE_SIZE` (4 KB) block:

```
+----------------------------------------------------------------+
| header | slot[0] slot[1] … slot[n-1] ▶   free   ◀ … rec1 rec0  |
+----------------------------------------------------------------+
 header = num_slots | free_ptr | table_id | page_lsn
 slot   = (offset, length)        length==0 ⇒ dead slot
```

- **Slot directory** grows downward from the header; **record payloads** grow
  upward from the end. Free space is the gap between them.
- Variable-length records (because `TEXT` is variable) are supported naturally.
- A deleted slot keeps its id (length set to 0) so that **RIDs
  `(page_id, slot_id)` stay stable** — the index and WAL reference rows by RID.
- `table_id` in the header makes a page self-describing: a heap finds its pages
  by their tag, so there is **no on-disk page linked list** to corrupt on crash.
- `page_lsn` records the last log record applied to the page; recovery uses it
  to make redo idempotent (§8).

### Heap files
`TableHeap` is the unordered set of pages tagged with a table's id. It keeps an
in-memory list of those page ids (rebuilt at startup by scanning), and offers
`find_page_with_room`, `allocate_page`, insert/update-in-place, and a full
`scan`. Inserts target the most-recently-used page first.

### Buffer pool
A fixed number of frames cache pages. Callers `fetch_page` (which **pins** the
page), work on it, then `unpin_page(dirty?)`. When full, the **least-recently-
used unpinned** page is evicted, written back first if dirty. Crucially, before
any dirty page is written to disk the buffer pool **flushes the WAL** — enforcing
the write-ahead rule that underpins recovery.

---

## 4. Indexing

### B+ tree design
A textbook B+ tree (`index/btree.py`) maps an integer key to **a list of RIDs**:

- **Internal nodes**: separator keys + child pointers (fan-out = `order`).
- **Leaf nodes**: sorted keys, each with a list of RIDs, plus a `next` pointer
  chaining leaves left-to-right for efficient range scans.
- **Insert** descends to a leaf and splits bottom-up on overflow, copying up a
  separator (leaf split) or pushing one up (internal split).

### Node structure & search path
A point lookup walks from the root: at each internal node `bisect_right(keys, k)`
selects the child; at the leaf `bisect_left` finds the key. Cost is **O(log N)**
in tree height (height 4 for 1,000 keys at order 8). Range scans locate the low
key then walk the leaf chain.

### Why a *multi-value* index (key → many RIDs)?
This is a direct consequence of the **MVCC** track. An `UPDATE` produces a new
row *version* with a new RID while the old version must survive for older
snapshots — both share the same key. The index therefore stores an entry per
version; an index lookup returns all candidate RIDs and the executor applies
visibility to pick the one version visible to the reader's snapshot. (The index
is memory-resident and rebuilt from the heap at startup — see Limitations.)

---

## 5. Query Execution

### Parser
A hand-written **lexer** + **recursive-descent parser** (`sql/`) cover
`CREATE TABLE`, `INSERT`, `SELECT` (with `JOIN`, `WHERE`, `GROUP BY`,
`ORDER BY`, `LIMIT`, aggregates, table aliases), `UPDATE`, `DELETE`, and
`BEGIN`/`COMMIT`/`ROLLBACK`. Expressions support comparisons and `AND`/`OR`.

### Plan generation
The optimizer turns the AST into a tree of physical operators (§6).

### Operator execution — the volcano model
Each operator is an iterator that pulls rows from its children on demand, so the
whole plan runs as a pipeline:

| Operator | Role |
|----------|------|
| `SeqScan` | scan a heap, apply visibility/locks |
| `IndexScan` | probe the B+ tree (point or range), then visibility |
| `Filter` | apply a `WHERE`/residual predicate |
| `NestedLoopJoin` | join by re-scanning the inner per outer row |
| `IndexNestedLoopJoin` | join by probing the inner's index per outer row |

Aggregation (`COUNT/SUM/MIN/MAX/AVG`, optional `GROUP BY`), sorting and limiting
are applied as a post-pass. Rows flow as dicts keyed by `alias.column`, so joins
merge cleanly and predicates resolve qualified or bare names.

---

## 6. Optimizer

A **cost-based** optimizer (`optimizer/optimizer.py`):

- **Selectivity estimation** from statistics (`row_count`, `ndistinct`):
  equality on a column ≈ `1/ndistinct`; ranges default to `0.3`.
- **Access-path selection**: for the driving table it compares an index probe
  (`R·selectivity + tree_height`) against a sequential scan (`R`) and picks the
  cheaper. Single-table predicates are **pushed down** to the scan.
- **Join algorithm**: an **Index-Nested-Loop** join when the inner table can be
  probed by its primary key, otherwise a plain nested-loop join.
- **Join order**: a greedy **left-deep** order that starts from the smallest
  estimated relation and prefers inners reachable by an index probe.

`EXPLAIN` prints the chosen operator tree **and** the planner's reasoning:

```
minidb> \explain SELECT u.name,o.amount FROM orders o JOIN users u ON u.id=o.uid WHERE o.amount>90
IndexNestedLoopJoin(inner=users)
  Filter
    SeqScan(orders AS o)

Planner notes:
  join order: start with orders (est~1 rows)
  orders: SeqScan (no usable index predicate)
  join users: IndexNestedLoopJoin probing id
```

---

## 7. Transactions & Concurrency

### Locking strategy (core: strict 2PL)
`txn/lock_manager.py` grants **shared (S)** and **exclusive (X)** locks per RID.
S/S is compatible; X conflicts with everything; an S held alone can upgrade to
X. Locks are held until commit/abort (**strict** 2PL) and released together,
which gives **serializable** isolation and avoids cascading aborts. A point
`UPDATE`/`DELETE` uses the PK index so it locks **only the target row** —
genuine row-level granularity.

A subtlety the stress tests forced us to get right: because writers create *new*
row versions, a 2PL reader must **lock the version, then re-read it under the
lock** and judge visibility against the *live* committed set. Reading from a
statement-start snapshot instead lets a reader return a value a committed writer
has already superseded — a lost update. Writes lock-then-read for the same
reason.

### Isolation guarantees
- **2PL** → serializable.
- **MVCC** → snapshot isolation / repeatable reads (each transaction reads a
  snapshot fixed at `BEGIN`).

### Primary-key uniqueness
Enforced on every insert by probing the PK index. A key held by a committed live
version is a hard duplicate error; a key being created/deleted by an *in-flight*
transaction is treated as a retryable conflict, which is what stops two live
versions of the same key arising under concurrent insert/delete.

### Deadlock handling
Before a transaction blocks, the lock manager records what it waits for in a
**wait-for graph** and runs cycle detection. If granting the wait would close a
cycle, the requesting transaction is chosen as victim and raises
`DeadlockError`, which the engine turns into an abort. Demonstrated by two
threads locking two rows in opposite order (`tests/test_transactions.py`).

---

## 8. Recovery

### WAL design
`recovery/wal.py` is an append-only log of LSN-stamped records. It is buffered
and flushed to disk **on every commit** and **before any dirty data page is
written** (the write-ahead invariant, enforced by the buffer pool).

### Log records
Minimal and uniform:
`BEGIN` · `COMMIT` · `ABORT` · `NEWPAGE(page_id, table_id)` ·
`INSERT(page_id, slot, after)` · `UPDATE(page_id, slot, before, after)` ·
`CHECKPOINT`. Every higher-level operation reduces to these — a SQL `DELETE` or
an MVCC supersede is an `UPDATE` that flips `xmax`; a SQL `UPDATE` is a supersede
plus an `INSERT`.

### Crash recovery procedure (redo-only, repeat history)
1. **Analysis** — classify transactions into *winners* (have `COMMIT`) and
   *losers* (started, never committed).
2. **Redo (repeat history)** — replay every `NEWPAGE/INSERT/UPDATE` in LSN
   order, **guarded by `page_lsn`**: skip a record whose LSN ≤ the page's LSN
   because that change is already on the page. This makes redo idempotent
   regardless of which pages were flushed, and rebuilds the exact crash-time
   bytes.

**Why no undo pass?** Classical ARIES would now physically undo losers by
restoring before-images — but on a *versioned* heap that is actually incorrect.
If a loser marks a row deleted (`xmax = loser`) and a later committed
transaction re-supersedes that same row (`xmax = T2`), writing the loser's
before-image back would clobber `T2`'s committed change and **resurrect** the
row. Instead MVCC visibility neutralises losers for free: a version *created* by
a loser (`xmin = loser`) and a deletion *made* by a loser (`xmax = loser`) are
never in the committed set, so they are invisible to everyone. Redo + the
recovered committed set is therefore sufficient and correct. (This was a real
bug caught by the concurrent bank-transfer stress test — see §10.)

Result: **committed transactions are preserved; uncommitted ones leave no
trace** — verified by `tests/test_recovery.py`, the concurrency stress tests,
and `benchmarks/demo_recovery.py`.

---

## 9. Extension Track B — MVCC

### Motivation
Under 2PL, readers and writers block each other: a reader must wait for a
writer's X lock to release. Read-heavy workloads suffer. MVCC removes
read–write blocking entirely by keeping multiple versions of each row.

### Design
- Every heap record carries a **version header `(xmin, xmax)`** — the
  transaction that created the version and the one that deleted/superseded it.
- A transaction takes a **snapshot** (the set of committed txn ids) at `BEGIN`.
- **Visibility** (`txn/mvcc.py`): a version is visible iff its creator is in the
  snapshot (or is me) **and** its deleter is not (or is unset). This yields
  snapshot isolation with **zero read locks**.
- **Writers** create a new version (`INSERT`) and stamp `xmax` on the old one
  (`UPDATE`) — both WAL-logged. A **first-updater-wins** check aborts a
  transaction that tries to overwrite a version already superseded since its
  snapshot (`WriteConflictError`).
- The same versioned heap also backs 2PL, so the only difference between modes
  is *what set of versions a read may see* and *whether reads take locks*.

### Results
See §10 — MVCC sustains **~1.4× higher read throughput** and **~1.5× lower tail
latency** than 2PL under read/write contention, while never blocking readers.

---

## 10. Benchmarks

**Setup:** Python 3.14, single machine. Scripts in `benchmarks/`. Numbers below
are representative runs (your hardware will vary).

> Honest caveat: CPython's GIL serialises CPU work, so the concurrency benchmark
> isolates **lock-induced blocking / latency**, not multi-core speedup — which is
> exactly the MVCC-vs-2PL distinction.

### (a) MVCC vs 2PL under contention — `bench_mvcc_vs_2pl.py`
8 readers + 2 writers, 1,000 rows, 50 hot keys, writers hold a lock ~2 ms.

| mode | reads/s | writes/s | read p50 (ms) | read p99 (ms) |
|------|--------:|---------:|--------------:|--------------:|
| 2PL  | 7,088 | 56 | 0.122 | 35.8 |
| MVCC | 12,406 | 80 | 0.073 | 18.7 |

→ **MVCC read throughput ≈ 1.75× 2PL; tail latency ≈ 1.9× lower.** Readers in
2PL stall on writers' X locks (and must lock-and-re-read); MVCC readers never
block.

### (b) Index scan vs sequential scan — `bench_index_vs_scan.py`
300 equality point lookups; index on `id`, none on `v`.

| rows | index (ms) | seqscan (ms) | speedup |
|-----:|-----------:|-------------:|--------:|
| 1,000 | 0.023 | 2.91 | 128× |
| 5,000 | 0.024 | 14.1 | 601× |
| 20,000 | 0.036 | 56.8 | 1,568× |

→ Index latency is ~flat (**O(log N)**) while seq-scan grows linearly
(**O(N)**) — the quantitative reason the optimizer prefers the index.

### (c) Crash recovery — `demo_recovery.py`
Commits survive a simulated mid-transaction crash; the in-flight transaction is
fully rolled back. **PASS.**

### (d) Concurrency correctness — `tests/test_concurrency_stress.py`
Randomized multi-threaded stress tests assert *invariants* under contention, in
both modes:
- **Bank transfer** — many threads move money between accounts; the total is
  invariant (no money created or destroyed), in memory **and after a restart**.
- **Random INSERT/UPDATE/DELETE** on a small key space — at most one visible
  version per primary key (no duplicates), and the engine never corrupts or
  hangs.

These caught three real bugs that the single-threaded tests missed: a
non-thread-safe buffer pool/WAL/index, missing primary-key uniqueness, and an
incorrect recovery undo pass (see §8). All are fixed; the suite passes
repeatedly.

Reproduce:
```bash
python benchmarks/bench_mvcc_vs_2pl.py
python benchmarks/bench_index_vs_scan.py
python benchmarks/demo_recovery.py
```

---

## 11. Limitations

Deliberate simplifications (correctness preserved; documented for the viva):

- **Indexes are memory-resident** and rebuilt by scanning the heap at startup;
  they are not themselves WAL-logged. Disk-backed, logged index pages are future
  work. (Heap data *is* fully persisted and recovered.)
- **No vacuum/GC**: dead row versions and stale index entries are not reclaimed;
  visibility filters them. A real system needs background cleanup.
- **B+ tree does not merge/rebalance on delete** — it stays correct and
  searchable but can become sparse.
- **The catalog is a JSON sidecar**, not stored in system tables.
- **Writes are serialized by a single write latch** (reads are fully
  concurrent). Shared structures (buffer pool, WAL, B+ tree) are guarded by
  short physical latches so concurrent access is safe; logical isolation still
  comes from MVCC/2PL. Fine for demonstrating MVCC's *read* advantage;
  finer-grained write concurrency is future work.
- **Recovery loads the whole WAL into memory** and there is no log truncation;
  checkpoints are markers only.
- Scope: integer/text columns, single-column PK, inner joins. NULLs are stored
  (via a per-row null bitmap) and handled with simplified SQL three-valued
  logic — any comparison involving NULL is treated as "not true".

---

## 12. How to Run

Requirements: **Python 3.10+**, no third-party runtime dependencies.

### Interactive shell
```bash
cd src
python -m minidb.cli /tmp/mydb --mode mvcc      # or --mode 2pl
```
```sql
CREATE TABLE users (id INT PRIMARY KEY, name TEXT, age INT);
INSERT INTO users VALUES (1,'alice',30),(2,'bob',25);
SELECT * FROM users WHERE age > 26;
\explain SELECT * FROM users WHERE id = 1
\tables
\quit
```

### Use as a library
```python
import sys; sys.path.insert(0, "src")
from minidb.engine import Engine

db = Engine("/tmp/mydb", mode="mvcc")
db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
db.execute("INSERT INTO t VALUES (1,10),(2,20)")
print(db.execute("SELECT id, v FROM t WHERE id = 1").rows)   # [(1, 10)]
db.close()
```

### Tests
```bash
python -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q          # 23 tests
```

### Benchmarks
```bash
python benchmarks/bench_mvcc_vs_2pl.py
python benchmarks/bench_index_vs_scan.py
python benchmarks/demo_recovery.py
```
