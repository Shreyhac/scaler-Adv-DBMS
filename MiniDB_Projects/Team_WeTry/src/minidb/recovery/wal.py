"""Write-Ahead Log: an append-only file of LSN-tagged records.

Records are buffered and written on flush; the buffer pool flushes the WAL
before any dirty page reaches disk (the write-ahead rule). The record types are
minimal -- BEGIN/COMMIT/ABORT, NEWPAGE, INSERT(after), UPDATE(before, after),
CHECKPOINT -- and everything reduces to them: a DELETE/supersede is an UPDATE
that flips xmax, and a SQL UPDATE is a supersede plus an INSERT.

On-disk layout (little-endian):
    uint32 total_len | uint64 lsn | uint8 type | uint64 txn_id | <type-specific>
"""

import os
import struct
import threading
from dataclasses import dataclass
from typing import Optional

BEGIN = 1
INSERT = 2
UPDATE = 3
COMMIT = 4
ABORT = 5
NEWPAGE = 6
CHECKPOINT = 7

_TYPE_NAMES = {
    BEGIN: "BEGIN", INSERT: "INSERT", UPDATE: "UPDATE", COMMIT: "COMMIT",
    ABORT: "ABORT", NEWPAGE: "NEWPAGE", CHECKPOINT: "CHECKPOINT",
}


@dataclass
class LogRecord:
    lsn: int
    type: int
    txn_id: int
    page_id: int = -1
    slot_id: int = 0
    table_id: int = 0
    before: Optional[bytes] = None
    after: Optional[bytes] = None

    @property
    def type_name(self):
        return _TYPE_NAMES.get(self.type, str(self.type))


def _pack_bytes(b):
    b = b or b""
    return struct.pack("<I", len(b)) + b


def _unpack_bytes(buf, off):
    (n,) = struct.unpack_from("<I", buf, off)
    off += 4
    return buf[off:off + n], off + n


def _serialize(rec):
    body = struct.pack("<QBQ", rec.lsn, rec.type, rec.txn_id)
    if rec.type in (INSERT, UPDATE):
        body += struct.pack("<iH", rec.page_id, rec.slot_id)
        if rec.type == UPDATE:
            body += _pack_bytes(rec.before)
        body += _pack_bytes(rec.after)
    elif rec.type == NEWPAGE:
        body += struct.pack("<ii", rec.page_id, rec.table_id)
    return struct.pack("<I", len(body)) + body


def _deserialize(buf, off):
    (total_len,) = struct.unpack_from("<I", buf, off)
    off += 4
    end = off + total_len
    lsn, rtype, txn_id = struct.unpack_from("<QBQ", buf, off)
    p = off + struct.calcsize("<QBQ")
    rec = LogRecord(lsn=lsn, type=rtype, txn_id=txn_id)
    if rtype in (INSERT, UPDATE):
        rec.page_id, rec.slot_id = struct.unpack_from("<iH", buf, p)
        p += struct.calcsize("<iH")
        if rtype == UPDATE:
            rec.before, p = _unpack_bytes(buf, p)
        rec.after, p = _unpack_bytes(buf, p)
    elif rtype == NEWPAGE:
        rec.page_id, rec.table_id = struct.unpack_from("<ii", buf, p)
    return rec, end


class WriteAheadLog:
    def __init__(self, path):
        self.path = path
        if not os.path.exists(path):
            open(path, "wb").close()
        self._f = open(path, "r+b")
        self._buffer = bytearray()
        # Guards the append buffer, LSN counter and file handle. Log records are
        # appended both under the engine write latch (mutations) and outside it
        # (commit), and the buffer pool flushes the log from its own latch, so
        # the WAL needs its own lock to stay consistent under concurrency.
        self._lock = threading.RLock()
        self._next_lsn = self._scan_max_lsn() + 1

    def _scan_max_lsn(self):
        max_lsn = 0
        for rec in self.read_all():
            max_lsn = max(max_lsn, rec.lsn)
        return max_lsn

    # ---- append API (returns the new record's LSN) ----------------------
    def _append(self, rec):
        with self._lock:
            rec.lsn = self._next_lsn
            self._next_lsn += 1
            self._buffer += _serialize(rec)
            return rec.lsn

    def log_begin(self, txn_id):
        return self._append(LogRecord(0, BEGIN, txn_id))

    def log_commit(self, txn_id):
        with self._lock:
            lsn = self._append(LogRecord(0, COMMIT, txn_id))
            self.flush()             # commit is a durability point
            return lsn

    def log_abort(self, txn_id):
        return self._append(LogRecord(0, ABORT, txn_id))

    def log_newpage(self, txn_id, page_id, table_id):
        return self._append(LogRecord(0, NEWPAGE, txn_id,
                                      page_id=page_id, table_id=table_id))

    def log_insert(self, txn_id, page_id, slot_id, after):
        return self._append(LogRecord(0, INSERT, txn_id,
                                      page_id=page_id, slot_id=slot_id, after=after))

    def log_update(self, txn_id, page_id, slot_id, before, after):
        return self._append(LogRecord(0, UPDATE, txn_id, page_id=page_id,
                                      slot_id=slot_id, before=before, after=after))

    def log_checkpoint(self):
        lsn = self._append(LogRecord(0, CHECKPOINT, 0))
        self.flush()
        return lsn

    # ---- durability -----------------------------------------------------
    def flush(self):
        with self._lock:
            if self._buffer:
                self._f.write(bytes(self._buffer))
                self._f.flush()
                os.fsync(self._f.fileno())
                self._buffer.clear()

    def close(self):
        self.flush()
        if not self._f.closed:
            self._f.close()

    # ---- reading (recovery) --------------------------------------------
    def read_all(self):
        """Yield every LogRecord currently on disk, in order."""
        self._f.seek(0)
        buf = self._f.read()
        off = 0
        n = len(buf)
        while off < n:
            if off + 4 > n:
                break
            (total_len,) = struct.unpack_from("<I", buf, off)
            if off + 4 + total_len > n:
                break               # torn final record from a crash mid-write
            rec, off = _deserialize(buf, off)
            yield rec
