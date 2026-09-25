"""Frame-level encode/decode: the fixed 12-byte header plus opaque payload.

This module knows nothing about requests or responses -- it only knows how
to wrap/unwrap a payload of bytes inside the fixed header described in
PROTOCOL.md. request.py and response.py build on top of it.
"""

import socket
from dataclasses import dataclass

from . import constants as C
from .errors import ProtocolError
from .io_utils import read_exact, write_all


@dataclass(frozen=True)
class RawFrame:
    frame_type: int
    flags: int
    payload: bytes
    header_bytes: bytes  # the raw 12 bytes as read off the wire, for -v dumps


def encode_frame(frame_type: int, payload: bytes, flags: int = 0) -> bytes:
    """Wrap payload in the fixed 12-byte header. Does not send anything."""
    if len(payload) > C.MAX_PAYLOAD_SIZE:
        raise ProtocolError(
            f"payload of {len(payload)} bytes exceeds MAX_PAYLOAD_SIZE ({C.MAX_PAYLOAD_SIZE})"
        )
    header = C.HEADER_STRUCT.pack(C.MAGIC, C.VERSION, frame_type, flags & 0xFF, 0, len(payload))
    return header + payload


def write_frame(sock: socket.socket, frame_type: int, payload: bytes, flags: int = 0) -> None:
    write_all(sock, encode_frame(frame_type, payload, flags))


def _decode_header(raw: bytes) -> tuple:
    """Validate and unpack a 12-byte header. Raises ProtocolError on any
    structural problem: bad magic, unsupported version, or an oversized
    length claim. Returns (frame_type, flags, payload_length)."""
    magic, version, frame_type, flags, reserved, length = C.HEADER_STRUCT.unpack(raw)

    if magic != C.MAGIC:
        raise ProtocolError(f"bad magic: expected {C.MAGIC!r}, got {magic!r}")
    if version != C.VERSION:
        raise ProtocolError(f"unsupported protocol version: {version}")
    if length > C.MAX_PAYLOAD_SIZE:
        raise ProtocolError(
            f"declared payload length {length} exceeds MAX_PAYLOAD_SIZE ({C.MAX_PAYLOAD_SIZE})"
        )
    # `reserved` is not validated strictly: a future version may define it,
    # and rejecting on it would defeat forward-compatibility. We only read
    # it far enough to keep it out of the type/flags fields.
    return frame_type, flags, length


def read_raw_frame(sock: socket.socket) -> RawFrame:
    """Read exactly one frame off the wire: header, then exactly
    payload_length bytes of payload, regardless of frame type. This is the
    single place that decides how many bytes constitute "one frame", which
    is what keeps the stream synchronized even for frame types nobody
    recognises.

    Raises ConnectionClosed if the peer closes cleanly before/at a frame
    boundary. Raises ProtocolError for a structurally invalid header.
    """
    header_bytes = read_exact(sock, C.HEADER_SIZE)
    frame_type, flags, length = _decode_header(header_bytes)
    payload = read_exact(sock, length)
    return RawFrame(frame_type=frame_type, flags=flags, payload=payload, header_bytes=header_bytes)


def read_known_frame(sock: socket.socket, expected_types: frozenset) -> RawFrame:
    """Read frames until one matches expected_types, transparently
    discarding any frame whose type is not recognised.

    This is the required "unknown frame type" behavior: read_raw_frame()
    already consumes exactly payload_length bytes for every frame it reads,
    known or not, so silently looping past an unrecognised type can never
    desynchronize the stream -- the bytes belonging to the next frame are
    never touched.
    """
    while True:
        frame = read_raw_frame(sock)
        if frame.frame_type in expected_types:
            return frame
        # Unknown (or, for this endpoint, out-of-context known) frame type:
        # already fully consumed above. Skip cleanly and keep reading.
        continue
