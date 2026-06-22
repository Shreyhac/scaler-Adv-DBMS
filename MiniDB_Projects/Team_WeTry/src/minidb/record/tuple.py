"""Tuple (row) serialization: [ version header | payload ] in a page slot.

The version header is the MVCC bookkeeping shared by both CC modes -- xmin (txn
that created the version) and xmax (txn that deleted/superseded it, 0 if live).
The payload encodes columns per the schema: INT as 8-byte signed little-endian,
TEXT as a 2-byte length prefix plus UTF-8 bytes (see serialize_values for the
null bitmap that precedes them).
"""

import struct

from .schema import INT, TEXT

# xmin (uint64), xmax (uint64)
_META_FORMAT = "<QQ"
META_SIZE = struct.calcsize(_META_FORMAT)


class TupleMeta:
    __slots__ = ("xmin", "xmax")

    def __init__(self, xmin=0, xmax=0):
        self.xmin = xmin
        self.xmax = xmax

    def pack(self):
        return struct.pack(_META_FORMAT, self.xmin, self.xmax)

    @staticmethod
    def unpack(buf):
        xmin, xmax = struct.unpack_from(_META_FORMAT, buf, 0)
        return TupleMeta(xmin, xmax)

    def __repr__(self):
        return f"TupleMeta(xmin={self.xmin}, xmax={self.xmax})"


def serialize_values(schema, values):
    """Serialize a list of python values into payload bytes per the schema.

    Layout: a null bitmap (1 bit per column, ceil(n/8) bytes) followed by the
    non-null values. A NULL column sets its bit and contributes no value bytes,
    so NULLs cost ~1 bit instead of a full column."""
    n = len(schema.columns)
    if len(values) != n:
        raise ValueError(f"expected {n} values, got {len(values)}")
    bitmap = bytearray((n + 7) // 8)
    out = bytearray()
    for i, (col, val) in enumerate(zip(schema.columns, values)):
        if val is None:
            bitmap[i // 8] |= 1 << (i % 8)
            continue
        if col.type == INT:
            out += struct.pack("<q", int(val))
        elif col.type == TEXT:
            b = str(val).encode("utf-8")
            out += struct.pack("<H", len(b)) + b
    return bytes(bitmap) + bytes(out)


def deserialize_values(schema, payload):
    """Inverse of serialize_values."""
    n = len(schema.columns)
    nbytes = (n + 7) // 8
    bitmap = payload[:nbytes]
    off = nbytes
    values = []
    for i, col in enumerate(schema.columns):
        if bitmap[i // 8] & (1 << (i % 8)):
            values.append(None)
            continue
        if col.type == INT:
            (v,) = struct.unpack_from("<q", payload, off)
            off += 8
            values.append(v)
        elif col.type == TEXT:
            (length,) = struct.unpack_from("<H", payload, off)
            off += 2
            v = payload[off:off + length].decode("utf-8")
            off += length
            values.append(v)
    return values


def pack_record(meta, schema, values):
    """Build the full record bytes (version header + payload)."""
    return meta.pack() + serialize_values(schema, values)


def unpack_record(record_bytes, schema):
    """Split record bytes into (TupleMeta, [values])."""
    meta = TupleMeta.unpack(record_bytes)
    values = deserialize_values(schema, record_bytes[META_SIZE:])
    return meta, values


def record_with_meta(record_bytes, new_meta):
    """Return a copy of record_bytes with the version header replaced. The
    payload (and therefore the total length) is unchanged, so the result can be
    written back in place with Page.update_tuple."""
    return new_meta.pack() + record_bytes[META_SIZE:]
