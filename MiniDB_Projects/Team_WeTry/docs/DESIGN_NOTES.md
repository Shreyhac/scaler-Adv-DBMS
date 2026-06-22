# MiniDB — Design Notes & Viva Cheat-Sheet

Concise rationale for the key decisions, framed as "why this and not the
alternative." Pair with the module docstrings (every file documents its own
design).

## The one idea that ties it together: a versioned heap
Every stored record is `[xmin | xmax | payload]`. This single format powers
**both** concurrency modes:
- **2PL** uses locks for isolation; visibility just means "created by a
  committed txn and not deleted by one."
- **MVCC** uses the same `(xmin, xmax)` plus a per-txn snapshot for snapshot
  isolation, with no read locks.

Because the storage format is identical, the MVCC-vs-2PL benchmark is a fair
comparison and the extension is a *concurrency-control* change, not a rewrite.

## Storage
- **Slotted pages** because records are variable-length (`TEXT`). Slot ids are
  stable across deletes so RIDs stay valid for the index and WAL.
- **`table_id` in the page header** instead of an on-disk page linked list →
  nothing structural to corrupt on crash; a heap finds its pages by tag.
- **`page_lsn` in the header** is the hook that makes redo idempotent.

## Indexing
- **Key → list of RIDs** (not a single RID) specifically because MVCC keeps
  multiple versions per key. Visibility selects the right one on lookup.
- Memory-resident + rebuilt from the heap at startup: keeps the durability story
  about *one* thing (the heap + WAL) and avoids logging index pages. Trade-off
  noted in Limitations.

## Optimizer
- Cost model is intentionally simple (`row_count`, `ndistinct`, fixed default
  selectivities). The point is to *demonstrate the decision*: index vs scan, and
  index-NLJ vs NLJ, with the reasoning visible in `EXPLAIN`.
- Greedy left-deep join order starting from the smallest relation — the same
  heuristic backbone real planners build on, without the full DP search.

## Concurrency
- **Strict** 2PL (hold until commit) → serializable + no cascading aborts.
- Deadlocks: wait-for graph + cycle detection, victim = the txn that closes the
  cycle. Simple and always makes progress.
- 2PL reads must **lock then re-read** under the lock and judge against the live
  committed set — otherwise a reader can use a stale snapshot value a committed
  writer already superseded (a lost update). This was caught by the bank-transfer
  stress test.
- MVCC: snapshot at BEGIN; first-updater-wins conflict check on write.
- A single write latch serializes mutations (and WAL append) so the
  log/apply/stamp sequence is atomic. Reads never take it — that's what the MVCC
  benchmark exploits. Shared structures (buffer pool, WAL, B+ tree) each have a
  short physical latch so concurrent access can't corrupt them.
- **Primary-key uniqueness** is checked against the index, treating a key touched
  by an *in-flight* (uncommitted) transaction conservatively as a retryable
  conflict — that prevents two live versions of one key under concurrent
  insert/delete.

## Recovery (redo-only on a versioned heap)
- **Steal + no-force** buffer policy → committed pages may be unflushed (need
  redo) and uncommitted pages may be on disk (would normally need undo).
- But physical **undo is wrong on a versioned heap**: restoring a loser's
  before-image can clobber a later committed supersede and resurrect a row. So
  we redo everything and let **MVCC visibility** hide losers (their xmin/xmax are
  never committed). Redo is idempotent, so recovery is itself crash-safe.
- WAL invariant enforced in exactly one place: the buffer pool flushes the log
  before writing any data page.

## Likely viva questions → where to point
- *"Show committed data surviving a crash"* → `benchmarks/demo_recovery.py`.
- *"Prove MVCC doesn't block readers"* → `bench_mvcc_vs_2pl.py` (p99 latency).
- *"Why is the index faster?"* → `bench_index_vs_scan.py` (O(log N) vs O(N)).
- *"Walk a SELECT through the system"* → §2 data flow + `engine._select`.
- *"How does an UPDATE work under MVCC?"* → `engine._update` →
  `_supersede` (old) + `_insert_version` (new); visibility in `txn/mvcc.py`.
