"""'A receiver encountering a frame type it does not know MUST skip it
cleanly.' These tests prove that: an unknown frame type is fully consumed
(never crashes, never desyncs the stream) and the next known frame after it
is still read correctly.
"""

import random

from binhttp import constants as C
from binhttp.frame import encode_frame, read_known_frame, read_raw_frame
from tests.test_fragmentation import ChunkedSocket

UNKNOWN_TYPE_A = 0x7F
UNKNOWN_TYPE_B = 0xF0


class TestUnknownFrameType:
    def test_single_unknown_frame_is_skipped_and_does_not_raise(self):
        combined = encode_frame(UNKNOWN_TYPE_A, b"mystery-payload") + encode_frame(
            C.FRAME_TYPE_RESPONSE, b"real"
        )
        sock = ChunkedSocket(combined, chunk_sizes=[len(combined)])
        frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_RESPONSE}))
        assert frame.frame_type == C.FRAME_TYPE_RESPONSE
        assert frame.payload == b"real"

    def test_multiple_unknown_frames_in_a_row_are_all_skipped(self):
        combined = (
            encode_frame(UNKNOWN_TYPE_A, b"one")
            + encode_frame(UNKNOWN_TYPE_B, b"two-two")
            + encode_frame(0x99, b"")
            + encode_frame(C.FRAME_TYPE_REQUEST, b"the-request")
        )
        sock = ChunkedSocket(combined, chunk_sizes=[len(combined)])
        frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_REQUEST}))
        assert frame.payload == b"the-request"

    def test_unknown_frame_with_large_payload_does_not_corrupt_next_frame(self):
        large_unknown = encode_frame(UNKNOWN_TYPE_A, b"z" * 50_000)
        known = encode_frame(C.FRAME_TYPE_RESPONSE, b"after-large-unknown")
        combined = large_unknown + known
        sock = ChunkedSocket(combined, chunk_sizes=[7, 1000, len(combined)])
        frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_RESPONSE}))
        assert frame.payload == b"after-large-unknown"

    def test_unknown_frame_with_zero_length_payload(self):
        combined = encode_frame(UNKNOWN_TYPE_A, b"") + encode_frame(C.FRAME_TYPE_REQUEST, b"x")
        sock = ChunkedSocket(combined, chunk_sizes=[len(combined)])
        frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_REQUEST}))
        assert frame.payload == b"x"

    def test_unknown_frame_boundary_survives_random_chunking(self):
        combined = (
            encode_frame(0xAB, b"noise-noise-noise")
            + encode_frame(0xCD, b"more-noise")
            + encode_frame(C.FRAME_TYPE_RESPONSE, b"payload-of-interest")
        )
        for seed in range(20):
            sock = ChunkedSocket(combined, rng=random.Random(seed))
            frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_RESPONSE}))
            assert frame.payload == b"payload-of-interest"

    def test_raw_read_of_unknown_type_does_not_raise(self):
        """decode_header/read_raw_frame treat an unrecognised type byte as
        structurally valid -- only read_known_frame's caller decides
        whether to act on it. This is what makes 'skip cleanly' possible."""
        raw = encode_frame(0xEE, b"whatever")
        sock = ChunkedSocket(raw)
        frame = read_raw_frame(sock)
        assert frame.frame_type == 0xEE
        assert frame.payload == b"whatever"

    def test_interleaved_known_and_unknown_over_live_server(self, running_server):
        """End-to-end: inject a raw unknown-type frame onto a real TCP
        connection to a running server between two real requests, and
        confirm the server keeps answering correctly afterward."""
        import socket as socket_module

        from binhttp.frame import write_frame
        from binhttp.request import encode_request
        from binhttp.response import decode_response

        host, port = running_server
        sock = socket_module.create_connection((host, port), timeout=5)
        try:
            write_frame(sock, C.FRAME_TYPE_REQUEST, encode_request(C.METHOD_GET, "/index.html", []))
            frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_RESPONSE}))
            assert decode_response(frame.payload).status == 200

            # Client sends a frame type the server has never heard of.
            write_frame(sock, 0x55, b"an experimental frame from the future")

            # The server must still answer the next real request correctly.
            write_frame(sock, C.FRAME_TYPE_REQUEST, encode_request(C.METHOD_GET, "/nested/page.html", []))
            frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_RESPONSE}))
            resp = decode_response(frame.payload)
            assert resp.status == 200
            assert b"nested" in resp.body
        finally:
            sock.close()
