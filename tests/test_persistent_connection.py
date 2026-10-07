"""Persistent-connection behavior: many requests, one TCP connection.

These tests run against a real Server instance and a real socket -- no
fakes -- because the property under test (framing stays synchronized
across an arbitrary number of request/response round trips on one
connection) is precisely the kind of thing that a mock could hide bugs in.
"""

import socket

from binhttp import constants as C
from binhttp.frame import read_known_frame, write_frame
from binhttp.request import encode_request
from binhttp.response import decode_response


def _get(sock: socket.socket, path: str):
    write_frame(sock, C.FRAME_TYPE_REQUEST, encode_request(C.METHOD_GET, path, [("Host", "t")]))
    frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_RESPONSE}))
    return decode_response(frame.payload)


class TestPersistentConnection:
    def test_six_requests_over_one_connection(self, raw_connection):
        paths = [
            "/index.html",
            "/nested/page.html",
            "/index.html",
            "/does-not-exist.html",
            "/binary.bin",
            "/empty.txt",
        ]
        results = [(_get(raw_connection, p).status) for p in paths]
        assert results == [200, 200, 200, 404, 200, 200]

    def test_responses_correspond_to_requests_in_order(self, raw_connection):
        # Interleave a hit and a miss repeatedly; if framing ever slipped,
        # a 200 would show up where a 404 was expected or vice versa.
        expected = []
        for i in range(20):
            path = "/index.html" if i % 2 == 0 else "/missing.html"
            expected.append(200 if i % 2 == 0 else 404)
            resp = _get(raw_connection, path)
            assert resp.status == expected[-1], f"request {i} ({path}) got {resp.status}"

    def test_no_new_socket_needed_between_requests(self, raw_connection):
        fd_before = raw_connection.fileno()
        for _ in range(5):
            _get(raw_connection, "/index.html")
        assert raw_connection.fileno() == fd_before  # same underlying fd throughout

    def test_connection_survives_a_client_error_then_continues(self, raw_connection):
        """A malformed *request payload* (valid frame, bad content) must
        get a 400 without the server dropping the connection."""
        from binhttp.frame import write_frame as _write_frame

        # method byte + path_length that overruns the payload -> ProtocolError
        # at decode_request, but the *frame* itself is well-formed, so the
        # stream stays in sync.
        bad_payload = bytes([C.METHOD_GET]) + (60000).to_bytes(2, "big") + b"short"
        _write_frame(raw_connection, C.FRAME_TYPE_REQUEST, bad_payload)
        frame = read_known_frame(raw_connection, frozenset({C.FRAME_TYPE_RESPONSE}))
        resp = decode_response(frame.payload)
        assert resp.status == 400

        # Connection must still work afterward.
        resp = _get(raw_connection, "/index.html")
        assert resp.status == 200

    def test_invalid_utf8_path_gets_400_and_connection_survives(self, raw_connection):
        """Regression for the exact defect behind the brief's 400 rule: a
        GET whose path bytes are not valid UTF-8 (01 00 02 2f ff 00) used
        to raise an uncaught UnicodeDecodeError in decode_request and drop
        the connection with no response at all. Now it must return a 400
        and the connection must stay usable."""
        from binhttp.frame import write_frame as _write_frame

        bad_payload = bytes([C.METHOD_GET]) + (2).to_bytes(2, "big") + b"/\xff" + b"\x00"
        _write_frame(raw_connection, C.FRAME_TYPE_REQUEST, bad_payload)
        frame = read_known_frame(raw_connection, frozenset({C.FRAME_TYPE_RESPONSE}))
        resp = decode_response(frame.payload)
        assert resp.status == 400

        resp = _get(raw_connection, "/index.html")
        assert resp.status == 200

    def test_invalid_utf8_header_gets_400_and_connection_survives(self, raw_connection):
        """Same guarantee at the header layer: a well-formed frame whose
        header block contains non-UTF-8 bytes gets a 400, not a dropped
        connection."""
        from binhttp.frame import write_frame as _write_frame

        path = b"/index.html"
        bad_headers = bytes([1, C.HEADER_CUSTOM_MARKER, 2, 0xC0, 0xC0]) + (0).to_bytes(2, "big")
        bad_payload = bytes([C.METHOD_GET]) + (len(path)).to_bytes(2, "big") + path + bad_headers
        _write_frame(raw_connection, C.FRAME_TYPE_REQUEST, bad_payload)
        frame = read_known_frame(raw_connection, frozenset({C.FRAME_TYPE_RESPONSE}))
        resp = decode_response(frame.payload)
        assert resp.status == 400

        resp = _get(raw_connection, "/index.html")
        assert resp.status == 200

    def test_server_closes_cleanly_when_client_closes(self, running_server):
        host, port = running_server
        sock = socket.create_connection((host, port), timeout=5)
        resp = _get(sock, "/index.html")
        assert resp.status == 200
        sock.close()  # should not raise, and server thread should notice EOF

        # A brand new connection must still work (proves the server loop
        # is still healthy and didn't crash handling the close).
        sock2 = socket.create_connection((host, port), timeout=5)
        try:
            resp2 = _get(sock2, "/index.html")
            assert resp2.status == 200
        finally:
            sock2.close()

    def test_two_independent_connections_do_not_interfere(self, running_server):
        host, port = running_server
        a = socket.create_connection((host, port), timeout=5)
        b = socket.create_connection((host, port), timeout=5)
        try:
            for _ in range(5):
                assert _get(a, "/index.html").status == 200
                assert _get(b, "/missing.html").status == 404
        finally:
            a.close()
            b.close()
