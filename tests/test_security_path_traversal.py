"""Path traversal / filesystem-escape tests, both at the fsutil unit level
and end-to-end through a running server."""

import os
import socket

import pytest

from binhttp import constants as C
from binhttp.frame import read_known_frame, write_frame
from binhttp.fsutil import PathEscapesRoot, resolve_path
from binhttp.response import decode_response


def _get(host, port, raw_path_bytes: bytes):
    """Send a request with an arbitrary raw path, bypassing encode_request's
    own (trusted) path handling, to simulate an adversarial client."""
    import struct

    sock = socket.create_connection((host, port), timeout=5)
    try:
        payload = bytes([C.METHOD_GET]) + struct.pack(">H", len(raw_path_bytes)) + raw_path_bytes + b"\x00"
        write_frame(sock, C.FRAME_TYPE_REQUEST, payload)
        frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_RESPONSE}))
        return decode_response(frame.payload)
    finally:
        sock.close()


TRAVERSAL_PAYLOADS = [
    b"/../etc/passwd",
    b"/../../etc/passwd",
    b"/../../../../../../etc/passwd",
    b"/nested/../../etc/passwd",
    b"/./../../etc/passwd",
    b"/....//....//etc/passwd",
    b"/nested/../../../etc/passwd",
]


class TestResolvePathUnit:
    def test_simple_file_resolves_inside_root(self, www_root):
        resolved = resolve_path(str(www_root), "/index.html")
        assert resolved == os.path.realpath(str(www_root / "index.html"))

    def test_nested_file_resolves_inside_root(self, www_root):
        resolved = resolve_path(str(www_root), "/nested/page.html")
        assert resolved.startswith(os.path.realpath(str(www_root)))

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_dot_dot_traversal_never_resolves_outside_root(self, www_root, payload):
        # A leading '/' anchors normpath at the *virtual* root, so
        # '/../etc/passwd' collapses to '/etc/passwd' -- i.e. root/etc/passwd,
        # which is still safely inside root (and, in these tests, doesn't
        # exist there, so the server layer will 404 it -- see
        # TestTraversalEndToEnd). resolve_path() must never raise here
        # *and* must never point outside root; both properties are checked.
        resolved = resolve_path(str(www_root), payload.decode())
        root_real = os.path.realpath(str(www_root))
        assert resolved == root_real or resolved.startswith(root_real + os.sep)
        assert not resolved.startswith("/etc/")

    def test_bare_double_dot_at_root_collapses_harmlessly(self, www_root):
        # normpath('/../index.html') == '/index.html' -- this must resolve
        # to the real index.html inside root, not raise and not escape.
        resolved = resolve_path(str(www_root), "/../index.html")
        assert resolved == os.path.realpath(str(www_root / "index.html"))

    def test_null_byte_rejected(self, www_root):
        with pytest.raises(PathEscapesRoot):
            resolve_path(str(www_root), "/index.html\x00.png")

    def test_symlink_escape_rejected(self, www_root):
        escape_link = www_root / "escape.txt"
        if not escape_link.is_symlink():
            pytest.skip("symlinks unsupported on this filesystem")
        with pytest.raises(PathEscapesRoot):
            resolve_path(str(www_root), "/escape.txt")


class TestTraversalEndToEnd:
    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_traversal_attempts_get_404_not_the_file(self, running_server, payload):
        resp = _get(*running_server, payload)
        assert resp.status == 404
        assert b"root:" not in resp.body  # never leak /etc/passwd contents

    def test_absolute_looking_path_stays_inside_root(self, running_server):
        # The protocol's own path field is always root-relative by
        # definition (encode_request/decode_request both enforce a leading
        # '/'), so this exercises a path that *looks* like it wants
        # `/etc/passwd` at the OS level.
        resp = _get(*running_server, b"/etc/passwd")
        assert resp.status == 404

    def test_traversal_attempt_does_not_crash_server_or_break_connection(self, running_server):
        host, port = running_server
        for payload in TRAVERSAL_PAYLOADS:
            resp = _get(host, port, payload)
            assert resp.status == 404
        # server must still be responsive after all attempts
        resp = _get(host, port, b"/index.html")
        assert resp.status == 200
