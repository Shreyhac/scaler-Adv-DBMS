# Lab 1: File I/O — Kernel Journey via strace
**Name:** Shreyansh Arora | **Roll No:** 24BCS10252

## strace output (key syscalls)

```
openat(AT_FDCWD, "lab1_output.txt", O_CREAT|O_WRONLY|O_TRUNC, 0644) = 3
write(3, "Lab 1: Low-level file I/O in C++\n...", 108)               = 108
fsync(3)                                                               = 0
close(3)                                                               = 0
openat(AT_FDCWD, "lab1_output.txt", O_RDONLY)                         = 3
fstat(3, {st_mode=S_IFREG|0644, st_size=108, ...})                    = 0
read(3, "Lab 1: Low-level file I/O...", 511)                          = 108
read(3, "", 511)                                                       = 0   # EOF
close(3)                                                               = 0
```

## Write path: user buffer → disk

```
C++ write(fd, buf, n)
        │
        ▼  [user → kernel mode via syscall]
   VFS  sys_write()
        │   resolves fd → struct file → inode
        ▼
   Page Cache  (4 KB pages in RAM)
        │   data copied into dirty kernel page
        ▼  [on fsync() / writeback thread]
   Block device driver (SATA / NVMe)
        │   logical blocks → physical sectors
        ▼
   Physical disk
```

## Key syscalls

| Syscall  | What it does |
|----------|-------------|
| `openat` | Resolves path through VFS → inode; allocates fd in process table |
| `write`  | Copies user buffer into kernel page cache; marks page DIRTY |
| `fsync`  | Blocks until storage device confirms dirty pages are on disk |
| `close`  | Releases fd; decrements inode reference count |
| `read`   | Copies bytes from page cache (or disk on cold miss) to user buffer |

## Why the page cache matters

- `write()` returns immediately after copying to the page cache — not after hitting disk.
- The same physical pages are shared between processes that `mmap` the same file.
- `fsync()` is the only guarantee of durability. Without it, a crash after `write()` can lose data.
- Repeated `read()` on a recently-written file hits the page cache, avoiding disk I/O entirely.

## Inode journey on `openat`

1. Kernel splits the path into components and walks each directory inode.
2. Each directory entry maps filename → inode number.
3. Permissions checked against process UID/GID and inode `st_mode`.
4. A `struct file` is created, holding the current offset and a pointer to the inode.
5. An integer fd is allocated in the process's open-file table pointing to `struct file`.
