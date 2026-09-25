"""Interoperability proof: golden byte vectors, parsed two ways.

1. An independent, from-scratch parser written directly against
   PROTOCOL.md's field layout (not importing anything from binhttp.frame/
   request/response) -- this is what proves the spec itself is complete
   enough for a stranger to reimplement, which is the assignment's actual
   bar ("enough for a stranger").
2. Our own decode_request/decode_response, to confirm our implementation
   agrees with the independent parse.

It also confirms a real running bserve accepts the golden request bytes
sent raw over a socket (not built via encode_request), and that a real
bcurl-style client can parse a raw golden response frame.
"""

import socket
import struct

from binhttp import constants as C
from binhttp.frame import read_known_frame, write_all
from binhttp.request import decode_request
from binhttp.response import decode_response

from .fixtures.vectors import (
    GOLDEN_REQUEST_EXPECTED,
    GOLDEN_REQUEST_FRAME,
    GOLDEN_RESPONSE_EXPECTED,
    GOLDEN_RESPONSE_FRAME,
)


def independent_parse_frame(raw: bytes) -> dict:
    """A from-scratch parser that reads PROTOCOL.md, not binhttp/frame.py."""
    magic, version, frame_type, flags, reserved, length = struct.unpack(">4sBBBBI", raw[:12])
    payload = raw[12 : 12 + length]
    assert len(payload) == length, "declared length must match actual payload bytes"
    return {
        "magic": magic,
        "version": version,
        "frame_type": frame_type,
        "flags": flags,
        "payload_length": length,
        "payload": payload,
    }


def independent_parse_headers(buf: bytes, offset: int):
    count = buf[offset]
    offset += 1
    headers = []
    for _ in range(count):
        name_id = buf[offset]
        offset += 1
        if name_id == 0xFF:
            name_len = buf[offset]
            offset += 1
            name = buf[offset : offset + name_len].decode("utf-8")
            offset += name_len
        else:
            name = C.HEADER_TABLE[name_id]
        (value_len,) = struct.unpack_from(">H", buf, offset)
        offset += 2
        value = buf[offset : offset + value_len].decode("utf-8")
        offset += value_len
        headers.append((name, value))
    return headers, offset


def independent_parse_request_payload(payload: bytes) -> dict:
    method = payload[0]
    (path_len,) = struct.unpack_from(">H", payload, 1)
    path = payload[3 : 3 + path_len].decode("utf-8")
    headers, _ = independent_parse_headers(payload, 3 + path_len)
    return {"method": method, "path": path, "headers": headers}


def independent_parse_response_payload(payload: bytes) -> dict:
    (status,) = struct.unpack_from(">H", payload, 0)
    headers, offset = independent_parse_headers(payload, 2)
    (body_len,) = struct.unpack_from(">I", payload, offset)
    offset += 4
    body = payload[offset : offset + body_len]
    return {"status": status, "headers": headers, "body": body}


class TestGoldenVectorsIndependentParse:
    def test_request_frame_header_fields(self):
        parsed = independent_parse_frame(GOLDEN_REQUEST_FRAME)
        assert parsed["magic"] == GOLDEN_REQUEST_EXPECTED["magic"]
        assert parsed["version"] == GOLDEN_REQUEST_EXPECTED["version"]
        assert parsed["frame_type"] == GOLDEN_REQUEST_EXPECTED["frame_type"]
        assert parsed["payload_length"] == GOLDEN_REQUEST_EXPECTED["payload_length"]

    def test_request_payload_fields(self):
        parsed = independent_parse_frame(GOLDEN_REQUEST_FRAME)
        req = independent_parse_request_payload(parsed["payload"])
        assert req["method"] == GOLDEN_REQUEST_EXPECTED["method"]
        assert req["path"] == GOLDEN_REQUEST_EXPECTED["path"]
        assert req["headers"] == GOLDEN_REQUEST_EXPECTED["headers"]

    def test_response_frame_header_fields(self):
        parsed = independent_parse_frame(GOLDEN_RESPONSE_FRAME)
        assert parsed["frame_type"] == GOLDEN_RESPONSE_EXPECTED["frame_type"]
        assert parsed["payload_length"] == GOLDEN_RESPONSE_EXPECTED["payload_length"]

    def test_response_payload_fields(self):
        parsed = independent_parse_frame(GOLDEN_RESPONSE_FRAME)
        resp = independent_parse_response_payload(parsed["payload"])
        assert resp["status"] == GOLDEN_RESPONSE_EXPECTED["status"]
        assert resp["headers"] == GOLDEN_RESPONSE_EXPECTED["headers"]
        assert resp["body"] == GOLDEN_RESPONSE_EXPECTED["body"]


class TestGoldenVectorsAgainstOurImplementation:
    def test_our_decoder_agrees_with_independent_parse(self):
        parsed = independent_parse_frame(GOLDEN_REQUEST_FRAME)
        ours = decode_request(parsed["payload"])
        assert ours.method == GOLDEN_REQUEST_EXPECTED["method"]
        assert ours.path == GOLDEN_REQUEST_EXPECTED["path"]
        assert ours.headers == GOLDEN_REQUEST_EXPECTED["headers"]

        parsed_r = independent_parse_frame(GOLDEN_RESPONSE_FRAME)
        ours_r = decode_response(parsed_r["payload"])
        assert ours_r.status == GOLDEN_RESPONSE_EXPECTED["status"]
        assert ours_r.body == GOLDEN_RESPONSE_EXPECTED["body"]


class TestServerAcceptsHandCraftedBytes:
    def test_server_parses_raw_golden_request_not_built_by_our_encoder(self, running_server, www_root):
        """Proves bserve doesn't secretly depend on some artifact of how
        our own encode_request() builds bytes -- fabricate them exactly per
        PROTOCOL.md (with the request's path pointed at a file that exists
        in this test's www_root) and confirm the server still serves it."""
        host, port = running_server
        # Re-target the golden request's path at a file we know exists.
        raw = GOLDEN_REQUEST_FRAME  # path "/index.html", which every www_root fixture has
        sock = socket.create_connection((host, port), timeout=5)
        try:
            write_all(sock, raw)
            frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_RESPONSE}))
            resp = decode_response(frame.payload)
            assert resp.status == 200
            assert resp.body == (www_root / "index.html").read_bytes()
        finally:
            sock.close()

    def test_client_side_parses_raw_golden_response_bytes(self):
        """Simulates what bcurl does with bytes read off the wire, using a
        response frame that was never touched by encode_response()."""
        parsed = independent_parse_frame(GOLDEN_RESPONSE_FRAME)
        resp = decode_response(parsed["payload"])
        assert resp.status == 200
        assert resp.body == b"<html>ok</html>"
