"""TCP fragmentation tests: the protocol must never assume one recv() call
returns exactly one frame, or even a whole field. These tests use a fake
socket that hands back attacker/network-controlled chunk sizes instead of
whatever the OS happened to buffer, which is the only way to deterministically
exercise "header split across reads" and friends.
"""

import random
import struct

import pytest

from binhttp import constants as C
from binhttp.errors import ConnectionClosed, ProtocolError
from binhttp.frame import encode_frame, read_raw_frame
from binhttp.io_utils import read_exact


class ChunkedSocket:
    """A minimal stand-in for socket.socket that serves .recv() out of an
    in-memory buffer, split into caller-controlled (or random) chunk sizes,
    to simulate real TCP fragmentation deterministically."""

    def __init__(self, data: bytes, chunk_sizes=None, rng: random.Random = None):
        self._data = data
        self._pos = 0
        self._chunk_sizes = list(chunk_sizes) if chunk_sizes is not None else None
        self._rng = rng or random.Random(0)

    def recv(self, bufsize: int) -> bytes:
        if self._pos >= len(self._data):
            return b""
        if self._chunk_sizes:
            n = min(self._chunk_sizes.pop(0), bufsize, len(self._data) - self._pos)
            if n <= 0:
                n = 1
        else:
            n = min(self._rng.randint(1, max(1, bufsize)), len(self._data) - self._pos)
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


class TestReadExact:
    def test_one_byte_at_a_time(self):
        sock = ChunkedSocket(b"hello world", chunk_sizes=[1] * 11)
        assert read_exact(sock, 11) == b"hello world"

    def test_odd_sized_chunks(self):
        sock = ChunkedSocket(b"0123456789", chunk_sizes=[1, 1, 2, 1, 5])
        assert read_exact(sock, 10) == b"0123456789"

    def test_eof_before_enough_bytes_raises(self):
        sock = ChunkedSocket(b"abc")
        with pytest.raises(ConnectionClosed):
            read_exact(sock, 10)

    def test_zero_length_read_returns_empty_without_touching_socket(self):
        sock = ChunkedSocket(b"")
        assert read_exact(sock, 0) == b""


class TestFrameFragmentation:
    def test_header_split_across_many_reads(self):
        raw = encode_frame(C.FRAME_TYPE_REQUEST, b"payload-bytes")
        sock = ChunkedSocket(raw, chunk_sizes=[1] * len(raw))
        frame = read_raw_frame(sock)
        assert frame.frame_type == C.FRAME_TYPE_REQUEST
        assert frame.payload == b"payload-bytes"

    def test_payload_split_across_many_reads(self):
        payload = bytes(range(200))
        raw = encode_frame(C.FRAME_TYPE_RESPONSE, payload)
        sock = ChunkedSocket(raw, chunk_sizes=[3] * (len(raw) // 3 + 1))
        frame = read_raw_frame(sock)
        assert frame.payload == payload

    def test_random_chunk_sizes_many_trials(self):
        payload = b"the quick brown fox jumps over the lazy dog" * 37
        raw = encode_frame(C.FRAME_TYPE_REQUEST, payload)
        for seed in range(25):
            sock = ChunkedSocket(raw, rng=random.Random(seed))
            frame = read_raw_frame(sock)
            assert frame.payload == payload

    def test_two_frames_in_a_single_recv_chunk(self):
        frame_a = encode_frame(C.FRAME_TYPE_REQUEST, b"AAA")
        frame_b = encode_frame(C.FRAME_TYPE_RESPONSE, b"BBBBB")
        combined = frame_a + frame_b
        # A single oversized chunk_sizes entry forces recv() to hand back
        # both frames' bytes in one call, the way TCP coalescing would.
        sock = ChunkedSocket(combined, chunk_sizes=[len(combined)])

        first = read_raw_frame(sock)
        second = read_raw_frame(sock)

        assert first.frame_type == C.FRAME_TYPE_REQUEST
        assert first.payload == b"AAA"
        assert second.frame_type == C.FRAME_TYPE_RESPONSE
        assert second.payload == b"BBBBB"

    def test_three_frames_one_write_random_chunking(self):
        frames = [
            encode_frame(C.FRAME_TYPE_REQUEST, b"one"),
            encode_frame(C.FRAME_TYPE_RESPONSE, b"two-two"),
            encode_frame(C.FRAME_TYPE_REQUEST, b""),
        ]
        combined = b"".join(frames)
        for seed in range(10):
            sock = ChunkedSocket(combined, rng=random.Random(seed))
            decoded = [read_raw_frame(sock) for _ in range(3)]
            assert [f.payload for f in decoded] == [b"one", b"two-two", b""]

    def test_frame_immediately_followed_by_another_no_gap(self):
        combined = encode_frame(C.FRAME_TYPE_REQUEST, b"X") + encode_frame(C.FRAME_TYPE_REQUEST, b"Y")
        sock = ChunkedSocket(combined, chunk_sizes=[len(combined)])
        assert read_raw_frame(sock).payload == b"X"
        assert read_raw_frame(sock).payload == b"Y"

    def test_truncated_frame_raises_connection_closed(self):
        raw = encode_frame(C.FRAME_TYPE_REQUEST, b"complete-payload")
        truncated = raw[:-5]  # cut off the last 5 payload bytes
        sock = ChunkedSocket(truncated)
        with pytest.raises(ConnectionClosed):
            read_raw_frame(sock)

    def test_truncated_header_raises_connection_closed(self):
        raw = encode_frame(C.FRAME_TYPE_REQUEST, b"data")
        sock = ChunkedSocket(raw[:5])  # not even a full 12-byte header
        with pytest.raises(ConnectionClosed):
            read_raw_frame(sock)

    def test_invalid_declared_length_raises_protocol_error(self):
        bad_header = struct.pack(
            ">4sBBBBI", C.MAGIC, C.VERSION, C.FRAME_TYPE_REQUEST, 0, 0, C.MAX_PAYLOAD_SIZE + 100
        )
        sock = ChunkedSocket(bad_header)
        with pytest.raises(ProtocolError):
            read_raw_frame(sock)

    def test_zero_length_payload_frame(self):
        raw = encode_frame(C.FRAME_TYPE_REQUEST, b"")
        sock = ChunkedSocket(raw, chunk_sizes=[C.HEADER_SIZE])
        frame = read_raw_frame(sock)
        assert frame.payload == b""

    def test_max_allowed_payload_round_trips(self):
        payload = b"\xaa" * C.MAX_PAYLOAD_SIZE
        raw = encode_frame(C.FRAME_TYPE_RESPONSE, payload)
        sock = ChunkedSocket(raw, chunk_sizes=[len(raw)])
        frame = read_raw_frame(sock)
        assert len(frame.payload) == C.MAX_PAYLOAD_SIZE
