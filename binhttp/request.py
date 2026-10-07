"""REQUEST frame payload: method + path + headers.

Wire format (this is the payload that sits inside a FRAME_TYPE_REQUEST
frame; the 12-byte frame header and its length field are handled by
frame.py):

    method       : 1 byte     -- see constants.METHOD_*
    path_length  : 2 bytes, big-endian
    path         : path_length bytes, UTF-8, always starts with '/'
    headers      : header block, see headers.py
"""

import struct
from dataclasses import dataclass, field

from . import constants as C
from .errors import ProtocolError
from .headers import HeaderList, decode_headers, encode_headers


@dataclass(frozen=True)
class Request:
    method: int
    path: str
    headers: HeaderList = field(default_factory=list)


def encode_request(method: int, path: str, headers: HeaderList = ()) -> bytes:
    path_bytes = path.encode("utf-8")
    if len(path_bytes) > C.MAX_PATH_LENGTH:
        raise ProtocolError(f"path of {len(path_bytes)} bytes exceeds MAX_PATH_LENGTH")

    out = bytearray()
    out.append(method & 0xFF)
    out.extend(struct.pack(">H", len(path_bytes)))
    out.extend(path_bytes)
    out.extend(encode_headers(list(headers)))
    return bytes(out)


def decode_request(payload: bytes) -> Request:
    if len(payload) < 3:
        raise ProtocolError("request payload shorter than the fixed method+path_length fields")

    method = payload[0]
    (path_len,) = struct.unpack_from(">H", payload, 1)
    if path_len > C.MAX_PATH_LENGTH:
        raise ProtocolError(f"path length {path_len} exceeds MAX_PATH_LENGTH")

    offset = 3
    if offset + path_len > len(payload):
        raise ProtocolError("truncated request: path shorter than declared path_length")
    try:
        path = payload[offset : offset + path_len].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"path is not valid UTF-8: {exc}") from exc
    offset += path_len

    headers, offset = decode_headers(payload, offset)

    if not path.startswith("/"):
        raise ProtocolError(f"path must be absolute (start with '/'): {path!r}")

    return Request(method=method, path=path, headers=headers)
