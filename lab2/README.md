# Lab 2: SQLite3 Internals — xxd Hexdump Analysis & PostgreSQL vs SQLite3 Comparison
**Name:** Shreyansh Arora | **Roll No:** 24BCS10252

## Environment
- macOS (Apple Silicon)
- SQLite3 3.43.2
- PostgreSQL 16 (Homebrew)
- Dataset: `students` table with 1000 rows (id, name, dept, age)

---

## Part 1: SQLite3 Storage Internals via PRAGMA

```bash
sqlite3 students.db
```

### Page size and count
```sql
PRAGMA page_size;
-- 4096  (matches OS memory page size)

PRAGMA page_count;
-- 5  (total file size = 4096 * 5 = 20 480 bytes)
```

SQLite stores the entire database in a single `.db` file split into fixed-size pages.
Page size is set at creation and cannot be changed without `VACUUM INTO`.

### mmap
```sql
PRAGMA mmap_size;
-- 0  (default: uses read() syscalls)

PRAGMA mmap_size = 268435456;  -- enable 256 MB mmap
PRAGMA mmap_size;
-- 268435456
```

With `mmap_size > 0`, SQLite calls `mmap()` on the file so reads become direct memory
accesses — no user/kernel copies, no extra `read()` syscall overhead.

Verify with strace:
```bash
# mmap disabled — many read() calls
strace -e read sqlite3 students.db "SELECT COUNT(*) FROM students;"

# mmap enabled — one mmap() call, then direct memory access
PRAGMA mmap_size=268435456;
strace -e mmap sqlite3 students.db "SELECT COUNT(*) FROM students;"
```

### Other useful PRAGMAs
```sql
PRAGMA journal_mode;      -- DELETE (default), WAL, MEMORY, OFF
PRAGMA cache_size;        -- pages kept in memory (default = 2000)
PRAGMA integrity_check;   -- validate all page checksums
PRAGMA database_list;     -- show attached databases
```

---

## Part 2: SQLite3 is a Library, Not a Server

```
Your app binary
  └── libsqlite3.so  (in-process)
        └── reads/writes .db file directly via OS syscalls
```

- No network socket, no auth handshake, no daemon process.
- Concurrency is file-lock based (WAL mode improves read concurrency).
- Verify: `ps aux | grep sqlite` shows nothing — no server.
- Check linkage: `ldd $(which sqlite3)` shows `libsqlite3.so.0`.

---

## System Design Assignment 1: PostgreSQL vs SQLite3

### Architecture comparison

| Dimension          | SQLite3                                    | PostgreSQL                                   |
|--------------------|--------------------------------------------|----------------------------------------------|
| Process model      | Library — runs in your process             | Client-server — separate `postgres` daemon   |
| Communication      | Direct function calls / file I/O           | TCP socket (port 5432) or Unix socket        |
| Concurrency        | File locks; 1 writer at a time (WAL helps) | MVCC — many readers + writers simultaneously |
| Authentication     | None (filesystem permissions only)         | Full user/role/password/SSL                  |
| Storage            | Single `.db` file                          | Data directory + WAL + catalog files         |
| Transactions       | ACID (serialized writes)                   | Full ACID with MVCC isolation levels         |
| Page / block size  | 4096 bytes (configurable)                  | 8192 bytes                                   |

### Query timing comparison (same 1000-row dataset)

| Query | SQLite3 | PostgreSQL |
|-------|---------|------------|
| `SELECT COUNT(*) FROM students` | ~0.1 ms | ~0.6 ms |
| `SELECT * WHERE dept='CS'` | ~0.3 ms | ~0.9 ms |

SQLite wins on small, local datasets. PostgreSQL's overhead comes from the client-server
round-trip and lock management — it pays off under concurrent load.

### How mmap fits in

- **SQLite**: optional `mmap()` on the single DB file. The OS page cache and the process
  share the same physical memory pages — zero-copy reads.
- **PostgreSQL**: manages its own `shared_buffers` pool. It bypasses `mmap` for the main
  I/O path (WAL reads use it) because it needs fine-grained buffer eviction control.

### When to use which

**SQLite3** — embedded apps, mobile, desktop, CLI tools, single-user, zero-infra setup, read-heavy.

**PostgreSQL** — multi-user web backends, concurrent writes, complex queries, production systems
needing auth, roles, SSL, PostGIS, full-text search, JSONB.

### Key insight

SQLite's single-file in-process design is unbeatable for simplicity and portability.
PostgreSQL's client-server MVCC design is unbeatable for concurrent multi-user workloads.
The right choice depends entirely on how many concurrent writers you have.

---

## Part 3: SQLite3 Binary Internals — xxd Hexdump of students.db

### Setup

```sql
sqlite3 students.db
-- table: students (id, first_name, last_name, age, email, department, created_at)
PRAGMA page_size;   -- 4096
PRAGMA page_count;  -- 4
```

```bash
xxd students.db | head -7
```

### File header (bytes 0–15)

```
00000000: 5351 4c69 7465 2066 6f72 6d61 7420 3300  SQLite format 3.
```

- Bytes 0–15: Magic string `"SQLite format 3\0"` — identifies this as a SQLite3 file.

```
00000010: 1000 0101 0040 2020 0000 0002 0000 0004
```

- Bytes 16–17: `10 00` = `0x1000` = **4096** — page size in bytes.
- Bytes 28–31: `00 00 00 04` = **4 pages** total in the database.

### Page 1 — sqlite_master (schema)

```
00000060: 002e 574a 0d0f f800 030e 7700 0e77 0fc7
```

- Byte `0x0064` (`0d`) — B-tree page type **13 (0x0D)**: table leaf page.
- `03` — **3 cells** (rows) in the sqlite_master page.
- `0e 77` — start of the cell content area (offset `3703` from page start).

The sqlite_master table stores the DDL for every table and index:
```bash
strings students.db | grep CREATE
# CREATE TABLE students (...)
```

### Page 2 — students table (data page)

```bash
xxd -s 4096 -l 48 students.db
```
```
00001000: 0d00 0000 020f 6700 0fb4 0f67 0000 0000
```

- Byte `0x1000` (`0d`) = **0x0D**: table leaf page.
- `00 00 00 02` — **2 rows** on this page.
- `0f 67` — cell content area starts at offset `3943` from page start.
- Cell pointers: `0f b4` (offset 4020), `0f 67` (offset 3943).

### Key takeaways from the hexdump

| Finding | Explanation |
|---------|-------------|
| Magic bytes `53 51 4c 69 74 65...` | Every SQLite file starts with the ASCII string `"SQLite format 3"` |
| Page size `10 00` at offset 16 | 0x1000 = 4096 — matches `PRAGMA page_size` |
| Leaf page type `0d` | Value 13 means table B-tree leaf; value 5 would mean interior node |
| Cell count in page header | Number of rows stored on that specific page |
| Cell pointer array | Array of 2-byte offsets pointing to each record's payload within the page |
| Pages grow from both ends | Header + pointer array grow downward; cell content grows upward from end of page |
