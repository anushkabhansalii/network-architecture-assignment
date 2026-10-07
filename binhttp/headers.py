"""Header list encoding: HPACK's first two ideas, scaled down.

Real HPACK has a dynamic table and Huffman coding; this protocol only
needs the first two mechanisms it's built from:

  1. A static table of well-known names, referenced by a 1-byte id.
  2. A length-prefixed literal for anything not in that table.

Wire format for a header list (count-prefixed list of entries):

    header_count   : 1 byte
    entry[0..count):
        name_id     : 1 byte      -- index into HEADER_TABLE, or 0xFF
        [name_len]  : 1 byte      -- only present if name_id == 0xFF
        [name]      : name_len bytes, UTF-8  -- only if name_id == 0xFF
        value_len   : 2 bytes, big-endian
        value       : value_len bytes, UTF-8
"""

import struct
from typing import List, Tuple

from . import constants as C
from .errors import ProtocolError

HeaderList = List[Tuple[str, str]]


def encode_headers(headers: HeaderList) -> bytes:
    if len(headers) > C.MAX_HEADER_COUNT:
        raise ProtocolError(f"{len(headers)} headers exceeds MAX_HEADER_COUNT ({C.MAX_HEADER_COUNT})")
    out = bytearray()
    out.append(len(headers))
    for name, value in headers:
        value_bytes = value.encode("utf-8")
        if len(value_bytes) > C.MAX_HEADER_VALUE_LENGTH:
            raise ProtocolError(f"header value for {name!r} too long")

        name_id = C.HEADER_NAME_TO_ID.get(name.lower())
        if name_id is not None:
            out.append(name_id)
        else:
            name_bytes = name.encode("utf-8")
            if len(name_bytes) > C.MAX_HEADER_NAME_LENGTH:
                raise ProtocolError(f"header name {name!r} too long")
            out.append(C.HEADER_CUSTOM_MARKER)
            out.append(len(name_bytes))
            out.extend(name_bytes)

        out.extend(struct.pack(">H", len(value_bytes)))
        out.extend(value_bytes)
    return bytes(out)


def decode_headers(buf: bytes, offset: int) -> Tuple[HeaderList, int]:
    """Decode a header list starting at buf[offset]. Returns (headers,
    new_offset). Every bounds check raises ProtocolError rather than
    letting Python raise IndexError, so a truncated/malicious buffer
    always produces a clean 400, never a crash."""
    if offset >= len(buf):
        raise ProtocolError("truncated header block: missing count byte")
    count = buf[offset]
    offset += 1
    if count > C.MAX_HEADER_COUNT:
        raise ProtocolError(f"{count} headers exceeds MAX_HEADER_COUNT")

    headers: HeaderList = []
    for _ in range(count):
        if offset >= len(buf):
            raise ProtocolError("truncated header block: missing name id")
        name_id = buf[offset]
        offset += 1

        if name_id == C.HEADER_CUSTOM_MARKER:
            if offset >= len(buf):
                raise ProtocolError("truncated header block: missing name length")
            name_len = buf[offset]
            offset += 1
            if offset + name_len > len(buf):
                raise ProtocolError("truncated header block: missing name bytes")
            try:
                name = buf[offset : offset + name_len].decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ProtocolError(f"header name is not valid UTF-8: {exc}") from exc
            offset += name_len
        elif name_id < len(C.HEADER_TABLE):
            name = C.HEADER_TABLE[name_id]
        else:
            raise ProtocolError(f"unknown header table id {name_id}")

        if offset + 2 > len(buf):
            raise ProtocolError("truncated header block: missing value length")
        (value_len,) = struct.unpack_from(">H", buf, offset)
        offset += 2
        if offset + value_len > len(buf):
            raise ProtocolError("truncated header block: missing value bytes")
        try:
            value = buf[offset : offset + value_len].decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProtocolError(f"header value is not valid UTF-8: {exc}") from exc
        offset += value_len

        headers.append((name, value))

    return headers, offset


def get_header(headers: HeaderList, name: str) -> "str | None":
    """Case-insensitive lookup, first match wins."""
    target = name.lower()
    for n, v in headers:
        if n.lower() == target:
            return v
    return None
