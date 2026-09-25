"""Functional file-serving tests, end-to-end against a real running server."""

import socket

from binhttp import constants as C
from binhttp.frame import read_known_frame, write_frame
from binhttp.headers import get_header
from binhttp.request import encode_request
from binhttp.response import decode_response


def _get(host, port, path):
    sock = socket.create_connection((host, port), timeout=5)
    try:
        write_frame(sock, C.FRAME_TYPE_REQUEST, encode_request(C.METHOD_GET, path, [("Host", "t")]))
        frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_RESPONSE}))
        return decode_response(frame.payload)
    finally:
        sock.close()


class TestFileServing:
    def test_existing_file(self, running_server):
        resp = _get(*running_server, "/index.html")
        assert resp.status == 200
        assert resp.body == b"<html>hello</html>"

    def test_content_length_header_matches_body(self, running_server):
        resp = _get(*running_server, "/index.html")
        assert get_header(resp.headers, "Content-Length") == str(len(resp.body))

    def test_content_type_guessed_for_html(self, running_server):
        resp = _get(*running_server, "/index.html")
        assert get_header(resp.headers, "Content-Type") == "text/html"

    def test_missing_file_404(self, running_server):
        resp = _get(*running_server, "/nope.html")
        assert resp.status == 404
        assert resp.body == b""

    def test_nested_file(self, running_server):
        resp = _get(*running_server, "/nested/page.html")
        assert resp.status == 200
        assert resp.body == b"<html>nested</html>"

    def test_empty_file(self, running_server):
        resp = _get(*running_server, "/empty.txt")
        assert resp.status == 200
        assert resp.body == b""
        assert get_header(resp.headers, "Content-Length") == "0"

    def test_binary_file_byte_exact(self, running_server, www_root):
        resp = _get(*running_server, "/binary.bin")
        assert resp.status == 200
        on_disk = (www_root / "binary.bin").read_bytes()
        assert resp.body == on_disk
        # sanity: this fixture contains every byte value, including NUL and
        # non-UTF8 bytes, so an accidental text-mode round trip would show.
        assert 0x00 in resp.body and 0xFF in resp.body

    def test_root_path_serves_index(self, running_server):
        resp = _get(*running_server, "/")
        assert resp.status == 200
        assert resp.body == b"<html>hello</html>"

    def test_repeated_requests_are_idempotent(self, running_server):
        first = _get(*running_server, "/index.html")
        second = _get(*running_server, "/index.html")
        assert first.body == second.body
        assert first.status == second.status

    def test_post_like_unsupported_method_405(self, running_server):
        # There is no encode_request() helper for a non-GET method because
        # bcurl never sends one; hand-build the payload to prove the
        # server-side check works regardless.
        import struct

        host, port = running_server
        sock = socket.create_connection((host, port), timeout=5)
        try:
            path = b"/index.html"
            payload = bytes([0x02]) + struct.pack(">H", len(path)) + path + b"\x00"
            write_frame(sock, C.FRAME_TYPE_REQUEST, payload)
            frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_RESPONSE}))
            resp = decode_response(frame.payload)
            assert resp.status == 405
        finally:
            sock.close()
