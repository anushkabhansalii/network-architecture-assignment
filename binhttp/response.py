"""RESPONSE frame payload: status + headers + body.

Wire format (the payload inside a FRAME_TYPE_RESPONSE frame):

    status       : 2 bytes, big-endian  -- e.g. 200, 400, 404
    headers      : header block, see headers.py
    body_length  : 4 bytes, big-endian
    body         : body_length bytes, opaque (exact file bytes)
"""

import struct
from dataclasses import dataclass, field

from . import constants as C
from .errors import ProtocolError
from .headers import HeaderList, decode_headers, encode_headers


@dataclass(frozen=True)
class Response:
    status: int
    headers: HeaderList = field(default_factory=list)
    body: bytes = b""


def encode_response(status: int, headers: HeaderList, body: bytes) -> bytes:
    if len(body) > C.MAX_PAYLOAD_SIZE:
        raise ProtocolError(f"body of {len(body)} bytes exceeds MAX_PAYLOAD_SIZE")

    out = bytearray()
    out.extend(struct.pack(">H", status))
    out.extend(encode_headers(list(headers)))
    out.extend(struct.pack(">I", len(body)))
    out.extend(body)
    return bytes(out)


def decode_response(payload: bytes) -> Response:
    if len(payload) < 2:
        raise ProtocolError("response payload shorter than the fixed status field")

    (status,) = struct.unpack_from(">H", payload, 0)
    offset = 2

    headers, offset = decode_headers(payload, offset)

    if offset + 4 > len(payload):
        raise ProtocolError("truncated response: missing body_length field")
    (body_len,) = struct.unpack_from(">I", payload, offset)
    offset += 4

    if offset + body_len > len(payload):
        raise ProtocolError(
            f"truncated response: body_length={body_len} but only {len(payload) - offset} bytes remain"
        )
    body = payload[offset : offset + body_len]

    return Response(status=status, headers=headers, body=body)
