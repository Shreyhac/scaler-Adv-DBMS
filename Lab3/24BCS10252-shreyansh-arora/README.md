# Lab 3 — Clock Sweep Buffer Cache

**Name:** Shreyansh Arora  
**Roll No:** 24BCS10252

---

## Overview

This lab implements the **Clock Sweep** (Second-Chance) page replacement algorithm — the same strategy used by PostgreSQL's buffer pool manager in `freelist.c`.

The `ClockSweep<T>` template class maintains a fixed-size circular buffer of frames. Each frame tracks a **reference bit**. When a new page must be loaded into a full cache, the clock hand sweeps circularly: frames with `refBit = true` get a "second chance" (bit cleared, hand advances), and the first frame with `refBit = false` is chosen as the eviction victim.

---

## Implementation Details

| Feature | Detail |
|---|---|
| Template | `ClockSweep<T>` — works with any hashable key type |
| O(1) lookup | `std::unordered_map<T, std::size_t>` maps key → frame index |
| Eviction | Clock hand; clears reference bit on second pass (second-chance policy) |
| Background thread | Periodically ages frames by clearing reference bits |
| Thread safety | `std::mutex` + `std::condition_variable`; background thread joins cleanly on destruction |
| Move/Copy | Deleted — non-copyable, non-movable |

### API

```cpp
ClockSweep<int> cache(4);   // capacity = 4 frames

cache.putKey(42);           // insert; evicts victim if full
int v = cache.getKey(42);   // hit → returns 42, sets ref bit
int m = cache.getKey(99);   // miss → returns int{} (0)
```

### Eviction walkthrough

```
Slots: [A(ref=1), B(ref=0), C(ref=1), D(ref=0)]  hand=0
Insert E:
  hand=0 → A has ref=1 → clear bit, advance
  hand=1 → B has ref=0 → evict B, place E here
Result: [A(ref=1), E(ref=1), C(ref=1), D(ref=0)]  hand=2
```

---

## Build & Run

```bash
mkdir build && cd build
cmake ..
cmake --build .
./clock_sweep
```

Expected output covers 4 demos: basic put/get, eviction, duplicate insert, and string keys.

---

## PostgreSQL Connection

PostgreSQL's `freelist.c` (`StrategyGetBuffer`) uses the same idea:
- Each shared buffer has a `usage_count` (≤ 5) instead of a single bit.
- The clock hand decrements `usage_count` on each pass.
- A buffer with `usage_count == 0` is selected as the victim.
- This gives heavily-used buffers multiple "second chances" before eviction.
