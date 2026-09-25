"""Pure encode/decode round-trip tests -- no sockets involved."""

import struct

import pytest

from binhttp import constants as C
from binhttp.errors import ProtocolError
from binhttp.frame import _decode_header, encode_frame
from binhttp.headers import decode_headers, encode_headers
from binhttp.request import decode_request, encode_request
from binhttp.response import decode_response, encode_response


class TestFrameHeader:
    def test_round_trip(self):
        raw = encode_frame(C.FRAME_TYPE_REQUEST, b"hello", flags=0x02)
        frame_type, flags, length = _decode_header(raw[: C.HEADER_SIZE])
        assert frame_type == C.FRAME_TYPE_REQUEST
        assert flags == 0x02
        assert length == 5
        assert raw[C.HEADER_SIZE :] == b"hello"

    def test_header_is_exactly_12_bytes(self):
        assert C.HEADER_SIZE == 12

    def test_zero_length_payload(self):
        raw = encode_frame(C.FRAME_TYPE_RESPONSE, b"")
        _, _, length = _decode_header(raw[: C.HEADER_SIZE])
        assert length == 0
        assert len(raw) == C.HEADER_SIZE

    def test_max_payload_accepted(self):
        payload = b"x" * C.MAX_PAYLOAD_SIZE
        raw = encode_frame(C.FRAME_TYPE_RESPONSE, payload)
        assert len(raw) == C.HEADER_SIZE + C.MAX_PAYLOAD_SIZE

    def test_oversized_payload_rejected_at_encode(self):
        with pytest.raises(ProtocolError):
            encode_frame(C.FRAME_TYPE_RESPONSE, b"x" * (C.MAX_PAYLOAD_SIZE + 1))

    def test_bad_magic_rejected(self):
        bad = struct.pack(">4sBBBBI", b"XXXX", C.VERSION, C.FRAME_TYPE_REQUEST, 0, 0, 0)
        with pytest.raises(ProtocolError, match="magic"):
            _decode_header(bad)

    def test_bad_version_rejected(self):
        bad = struct.pack(">4sBBBBI", C.MAGIC, 99, C.FRAME_TYPE_REQUEST, 0, 0, 0)
        with pytest.raises(ProtocolError, match="version"):
            _decode_header(bad)

    def test_oversized_declared_length_rejected(self):
        bad = struct.pack(">4sBBBBI", C.MAGIC, C.VERSION, C.FRAME_TYPE_REQUEST, 0, 0, C.MAX_PAYLOAD_SIZE + 1)
        with pytest.raises(ProtocolError, match="MAX_PAYLOAD_SIZE"):
            _decode_header(bad)

    def test_unknown_frame_type_is_structurally_valid(self):
        """An unknown type byte must NOT raise -- the header is still
        well-formed; interpretation is a caller-level concern (frame.py's
        read_known_frame skips it)."""
        raw = struct.pack(">4sBBBBI", C.MAGIC, C.VERSION, 0xAB, 0, 0, 3) + b"xyz"
        frame_type, flags, length = _decode_header(raw[: C.HEADER_SIZE])
        assert frame_type == 0xAB
        assert length == 3


class TestHeaders:
    def test_known_name_uses_table_id(self):
        encoded = encode_headers([("Content-Type", "text/html")])
        # count(1) + id(1) + value_len(2) + value(9) = 13
        assert encoded[0] == 1
        assert encoded[1] == C.HEADER_NAME_TO_ID["content-type"]

    def test_custom_name_is_length_prefixed(self):
        encoded = encode_headers([("X-Custom", "value")])
        assert encoded[1] == C.HEADER_CUSTOM_MARKER
        name_len = encoded[2]
        assert name_len == len("X-Custom")
        assert encoded[3 : 3 + name_len] == b"X-Custom"

    def test_round_trip_mixed(self):
        headers = [("Host", "example.com"), ("X-Trace-Id", "abc123"), ("Accept", "*/*")]
        encoded = encode_headers(headers)
        decoded, offset = decode_headers(encoded, 0)
        assert decoded == headers
        assert offset == len(encoded)

    def test_empty_header_list(self):
        encoded = encode_headers([])
        assert encoded == b"\x00"
        decoded, offset = decode_headers(encoded, 0)
        assert decoded == []
        assert offset == 1

    def test_too_many_headers_rejected(self):
        headers = [(f"X-{i}", "v") for i in range(C.MAX_HEADER_COUNT + 1)]
        with pytest.raises(ProtocolError):
            encode_headers(headers)

    def test_decode_truncated_count_byte(self):
        with pytest.raises(ProtocolError):
            decode_headers(b"", 0)

    def test_decode_truncated_value(self):
        # count=1, id=0 (Host), value_len=100, but no value bytes follow
        buf = bytes([1, 0]) + struct.pack(">H", 100)
        with pytest.raises(ProtocolError):
            decode_headers(buf, 0)

    def test_decode_unknown_table_id_rejected(self):
        buf = bytes([1, 250]) + struct.pack(">H", 0)
        with pytest.raises(ProtocolError):
            decode_headers(buf, 0)


class TestRequest:
    def test_round_trip(self):
        payload = encode_request(C.METHOD_GET, "/index.html", [("Host", "localhost:9000")])
        req = decode_request(payload)
        assert req.method == C.METHOD_GET
        assert req.path == "/index.html"
        assert req.headers == [("Host", "localhost:9000")]

    def test_path_must_start_with_slash(self):
        # Hand-construct a payload with a non-absolute path to bypass the
        # encoder's own good behavior and exercise the decoder's check.
        path = b"index.html"
        payload = bytes([C.METHOD_GET]) + struct.pack(">H", len(path)) + path + b"\x00"
        with pytest.raises(ProtocolError, match="absolute"):
            decode_request(payload)

    def test_truncated_path_rejected(self):
        payload = bytes([C.METHOD_GET]) + struct.pack(">H", 50) + b"short"
        with pytest.raises(ProtocolError):
            decode_request(payload)

    def test_empty_payload_rejected(self):
        with pytest.raises(ProtocolError):
            decode_request(b"")

    def test_path_length_over_limit_rejected(self):
        with pytest.raises(ProtocolError):
            encode_request(C.METHOD_GET, "/" + "a" * C.MAX_PATH_LENGTH, [])


class TestResponse:
    def test_round_trip(self):
        payload = encode_response(200, [("Content-Type", "text/html")], b"<html></html>")
        resp = decode_response(payload)
        assert resp.status == 200
        assert resp.headers == [("Content-Type", "text/html")]
        assert resp.body == b"<html></html>"

    def test_empty_body(self):
        payload = encode_response(404, [], b"")
        resp = decode_response(payload)
        assert resp.body == b""

    def test_binary_body_exact_bytes(self):
        body = bytes(range(256))
        payload = encode_response(200, [], body)
        resp = decode_response(payload)
        assert resp.body == body

    def test_truncated_body_rejected(self):
        # body_length claims 100 bytes but none follow
        payload = struct.pack(">H", 200) + b"\x00" + struct.pack(">I", 100)
        with pytest.raises(ProtocolError):
            decode_response(payload)

    def test_missing_body_length_field_rejected(self):
        payload = struct.pack(">H", 200) + b"\x00"  # status + empty header count, nothing else
        with pytest.raises(ProtocolError):
            decode_response(payload)
