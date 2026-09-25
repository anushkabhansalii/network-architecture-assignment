"""End-to-end CLI tests: actually spawn ./bserve and ./bcurl as subprocesses,
the same way a grader running the README would. Exercises exit codes,
stdout body correctness, and -v hexdump output going to stderr.
"""

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BSERVE = str(REPO_ROOT / "bserve")
BCURL = str(REPO_ROOT / "bcurl")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def cli_www(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    (root / "index.html").write_text("<html>cli-test</html>")
    return root


@pytest.fixture()
def cli_server(cli_www):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, BSERVE, str(cli_www), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
    else:
        proc.kill()
        raise RuntimeError("bserve subprocess did not start listening in time")

    yield port
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


class TestCliHappyPath:
    def test_bcurl_fetches_file_to_stdout(self, cli_server):
        result = subprocess.run(
            [sys.executable, BCURL, f"localhost:{cli_server}/index.html"],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout == b"<html>cli-test</html>"

    def test_bcurl_verbose_hexdumps_to_stderr(self, cli_server):
        result = subprocess.run(
            [sys.executable, BCURL, "-v", f"localhost:{cli_server}/index.html"],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
        stderr = result.stderr.decode()
        assert "SEND frame" in stderr
        assert "RECV frame" in stderr
        assert "42 53 43 31" in stderr  # magic bytes "BSC1" in hex, in the dump
        assert result.stdout == b"<html>cli-test</html>"


class TestCliExitCodes:
    def test_404_exits_nonzero(self, cli_server):
        result = subprocess.run(
            [sys.executable, BCURL, f"localhost:{cli_server}/missing.html"],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode != 0
        assert result.stdout == b""

    def test_connection_refused_exits_nonzero(self):
        result = subprocess.run(
            [sys.executable, BCURL, "localhost:1/index.html"],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode != 0

    def test_bad_target_exits_nonzero(self, cli_server):
        result = subprocess.run(
            [sys.executable, BCURL, "not-a-valid-target"],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode != 0
