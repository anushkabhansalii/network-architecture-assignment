"""Fuzz / adversarial testing harness.

Goal: throw structurally-broken and semantically-hostile input at every
layer of the protocol (frame header, request payload, response payload,
header block) and prove the *only* possible outcomes are:

  - a clean ProtocolError (mapped to 400 by the server), or
  - a clean ConnectionClosed, or
  - a correctly-decoded value (for inputs that happen to be valid)

Never: an unhandled exception (IndexError, UnicodeDecodeError, struct.error,
MemoryError, ...), a hang, or a stream desync. Deterministic seeds keep the
suite reproducible in CI while still covering a wide, varied input space.
"""

import random
import socket
import struct

import pytest

from binhttp import constants as C
from binhttp.errors import ConnectionClosed, ProtocolError
from binhttp.frame import _decode_header, read_known_frame, read_raw_frame, write_all
from binhttp.headers import decode_headers
from binhttp.request import decode_request
from binhttp.response import decode_response
from tests.test_fragmentation import ChunkedSocket

N_TRIALS = 500


def random_bytes(rng: random.Random, n: int) -> bytes:
    return bytes(rng.randrange(256) for _ in range(n))


class TestFuzzFrameHeader:
    """Completely random 12-byte headers, plus targeted corruptions of a
    valid header. Every outcome must be ProtocolError, never a crash."""

    def test_fully_random_headers_never_crash(self):
        rng = random.Random(1234)
        for _ in range(N_TRIALS):
            header = random_bytes(rng, C.HEADER_SIZE)
            try:
                _decode_header(header)
            except ProtocolError:
                pass  # expected for almost all random input
            # any other exception type fails the test by propagating

    def test_corrupted_magic_always_rejected(self):
        rng = random.Random(1)
        good = struct.pack(">4sBBBBI", C.MAGIC, C.VERSION, C.FRAME_TYPE_REQUEST, 0, 0, 5)
        for _ in range(100):
            corrupted = bytearray(good)
            idx = rng.randrange(4)
            corrupted[idx] ^= 0xFF
            with pytest.raises(ProtocolError):
                _decode_header(bytes(corrupted))

    def test_every_invalid_version_rejected(self):
        for version in range(256):
            if version == C.VERSION:
                continue
            header = struct.pack(">4sBBBBI", C.MAGIC, version, C.FRAME_TYPE_REQUEST, 0, 0, 0)
            with pytest.raises(ProtocolError):
                _decode_header(header)

    def test_impossible_lengths_rejected(self):
        for length in (C.MAX_PAYLOAD_SIZE + 1, 0xFFFFFFFF, 2**31, 2**31 + 1):
            header = struct.pack(">4sBBBBI", C.MAGIC, C.VERSION, C.FRAME_TYPE_REQUEST, 0, 0, length)
            with pytest.raises(ProtocolError):
                _decode_header(header)

    def test_unknown_frame_types_all_structurally_valid(self):
        """Every byte value that isn't REQUEST/RESPONSE must still decode
        as a *header* without error -- rejecting it is read_known_frame's
        job, not _decode_header's, per the 'skip unknown types' rule."""
        for frame_type in range(256):
            if frame_type in C.KNOWN_FRAME_TYPES:
                continue
            header = struct.pack(">4sBBBBI", C.MAGIC, C.VERSION, frame_type, 0, 0, 0)
            ft, flags, length = _decode_header(header)
            assert ft == frame_type


class TestFuzzTruncatedAndOversizedStreams:
    def test_random_length_random_truncated_streams(self):
        rng = random.Random(99)
        for _ in range(N_TRIALS):
            n = rng.randrange(0, 40)
            sock = ChunkedSocket(random_bytes(rng, n))
            with pytest.raises((ProtocolError, ConnectionClosed)):
                read_raw_frame(sock)

    def test_valid_header_claiming_huge_length_but_no_payload(self):
        header = struct.pack(">4sBBBBI", C.MAGIC, C.VERSION, C.FRAME_TYPE_REQUEST, 0, 0, 1_000_000)
        sock = ChunkedSocket(header)  # no payload bytes follow at all
        with pytest.raises(ConnectionClosed):
            read_raw_frame(sock)

    def test_declared_length_exceeding_cap_rejected_before_reading_payload(self):
        """The point of rejecting at the header stage: a hostile length
        must never cause an attempted multi-gigabyte allocation/read."""
        header = struct.pack(">4sBBBBI", C.MAGIC, C.VERSION, C.FRAME_TYPE_REQUEST, 0, 0, 0xFFFFFFFE)
        sock = ChunkedSocket(header)
        with pytest.raises(ProtocolError):
            read_raw_frame(sock)


class TestFuzzHeaderBlock:
    def test_random_bytes_as_header_block_never_crash(self):
        rng = random.Random(42)
        for _ in range(N_TRIALS):
            buf = random_bytes(rng, rng.randrange(0, 64))
            try:
                decode_headers(buf, 0)
            except ProtocolError:
                pass

    def test_reserved_table_id_range_rejected(self):
        for name_id in range(len(C.HEADER_TABLE), C.HEADER_CUSTOM_MARKER):
            buf = bytes([1, name_id]) + struct.pack(">H", 0)
            with pytest.raises(ProtocolError):
                decode_headers(buf, 0)

    def test_malformed_custom_name_utf8_rejected_cleanly(self):
        # 0xFF marker, name_len=2, then an invalid UTF-8 byte pair.
        # Must be a ProtocolError specifically -- a leaked UnicodeDecodeError
        # would bypass the server's 400 path and kill the connection.
        buf = bytes([1, 0xFF, 2, 0xC0, 0xC0]) + struct.pack(">H", 0)
        with pytest.raises(ProtocolError):
            decode_headers(buf, 0)


class TestFuzzRequestPayload:
    def test_random_bytes_as_request_payload_never_crash(self):
        rng = random.Random(7)
        for _ in range(N_TRIALS):
            payload = random_bytes(rng, rng.randrange(0, 128))
            try:
                decode_request(payload)
            except ProtocolError:
                pass

    def test_strange_reserved_and_flag_combinations(self):
        rng = random.Random(11)
        for _ in range(100):
            flags = rng.randrange(256)
            reserved = rng.randrange(256)
            header = struct.pack(">4sBBBBI", C.MAGIC, C.VERSION, C.FRAME_TYPE_REQUEST, flags, reserved, 0)
            ft, f, length = _decode_header(header)
            assert length == 0  # reserved/flags must never influence length parsing


class TestFuzzResponsePayload:
    def test_random_bytes_as_response_payload_never_crash(self):
        rng = random.Random(77)
        for _ in range(N_TRIALS):
            payload = random_bytes(rng, rng.randrange(0, 128))
            try:
                decode_response(payload)
            except ProtocolError:
                pass


class TestFuzzUnexpectedFrameSequences:
    def test_random_sequence_of_valid_and_unknown_frames(self):
        """Build long random sequences mixing known and unknown frame
        types with random payloads, and confirm read_known_frame either
        returns a well-formed REQUEST/RESPONSE or raises cleanly -- never
        desyncs partway through."""
        rng = random.Random(2024)
        for trial in range(50):
            frames = []
            for _ in range(rng.randrange(1, 8)):
                frame_type = rng.choice([C.FRAME_TYPE_REQUEST, C.FRAME_TYPE_RESPONSE, 0x03, 0x7E, 0x80, 0xFF])
                payload = random_bytes(rng, rng.randrange(0, 30))
                header = struct.pack(">4sBBBBI", C.MAGIC, C.VERSION, frame_type, 0, 0, len(payload))
                frames.append(header + payload)
            combined = b"".join(frames)
            sock = ChunkedSocket(combined, rng=random.Random(trial))
            # Drain everything; must never raise anything but our own types.
            try:
                while True:
                    read_known_frame(sock, frozenset({C.FRAME_TYPE_REQUEST, C.FRAME_TYPE_RESPONSE}))
            except (ConnectionClosed, ProtocolError):
                pass


class TestFuzzLiveServer:
    """The same fuzz corpus, but fired at a real bserve over a real socket,
    to prove the server process itself survives (thread doesn't die,
    process doesn't crash, connection either gets a 400 or is cleanly
    closed) rather than just the pure functions."""

    def test_server_survives_garbage_connections(self, running_server):
        host, port = running_server
        rng = random.Random(555)
        for _ in range(60):
            sock = socket.create_connection((host, port), timeout=2)
            try:
                garbage = random_bytes(rng, rng.randrange(0, 64))
                try:
                    write_all(sock, garbage)
                except (ConnectionClosed, OSError):
                    pass
                sock.settimeout(1)
                try:
                    sock.recv(4096)
                except (OSError, socket.timeout):
                    pass
            finally:
                sock.close()

        # The server must still be alive and correct after the barrage.
        from binhttp.frame import write_frame
        from binhttp.request import encode_request

        sock = socket.create_connection((host, port), timeout=5)
        try:
            write_frame(sock, C.FRAME_TYPE_REQUEST, encode_request(C.METHOD_GET, "/index.html", []))
            frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_RESPONSE}))
            assert decode_response(frame.payload).status == 200
        finally:
            sock.close()

    def test_server_survives_oversized_length_claim(self, running_server):
        host, port = running_server
        header = struct.pack(">4sBBBBI", C.MAGIC, C.VERSION, C.FRAME_TYPE_REQUEST, 0, 0, 0xFFFFFFFE)
        sock = socket.create_connection((host, port), timeout=5)
        try:
            write_all(sock, header)  # header only, no payload -- server must reject, not hang/allocate
            sock.settimeout(2)
            try:
                sock.recv(4096)
            except (OSError, socket.timeout):
                pass
        finally:
            sock.close()

        from binhttp.frame import write_frame
        from binhttp.request import encode_request

        sock2 = socket.create_connection((host, port), timeout=5)
        try:
            write_frame(sock2, C.FRAME_TYPE_REQUEST, encode_request(C.METHOD_GET, "/index.html", []))
            frame = read_known_frame(sock2, frozenset({C.FRAME_TYPE_RESPONSE}))
            assert decode_response(frame.payload).status == 200
        finally:
            sock2.close()
