import os
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binhttp.server import Server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def www_root(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    (root / "index.html").write_text("<html>hello</html>")
    (root / "empty.txt").write_text("")
    nested = root / "nested"
    nested.mkdir()
    (nested / "page.html").write_text("<html>nested</html>")
    # a real (small, non-UTF8) binary fixture
    (root / "binary.bin").write_bytes(bytes(range(256)) * 4)

    # a symlink that tries to point back out of the root
    outside = tmp_path / "outside.txt"
    outside.write_text("should never be reachable")
    try:
        (root / "escape.txt").symlink_to(outside)
    except OSError:
        pass  # symlinks unsupported on this filesystem; traversal test skips it

    return root


@pytest.fixture()
def running_server(www_root):
    port = _free_port()
    server = Server(root=str(www_root), host="127.0.0.1", port=port, verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    deadline = time.time() + 2
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)
    else:
        raise RuntimeError("server did not start in time")

    yield "127.0.0.1", port


@pytest.fixture()
def raw_connection(running_server):
    host, port = running_server
    sock = socket.create_connection((host, port), timeout=5)
    yield sock
    sock.close()
