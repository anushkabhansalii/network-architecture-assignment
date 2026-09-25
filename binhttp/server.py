"""bserve: the server half of the protocol.

    ./bserve ./www 9000

Accepts TCP connections, and on each one reads binary REQUEST frames in a
loop, replying with a RESPONSE frame to each, until the client closes the
connection. One connection may carry any number of requests.
"""

import argparse
import socket
import sys
import threading

from . import constants as C
from .errors import ConnectionClosed, ProtocolError
from .frame import read_known_frame, write_frame
from .fsutil import PathEscapesRoot, guess_content_type, read_file, resolve_path
from .request import decode_request
from .response import Response, encode_response


def handle_request(root: str, request) -> Response:
    if request.method != C.METHOD_GET:
        return Response(status=C.STATUS_METHOD_NOT_ALLOWED, headers=[], body=b"")

    try:
        fs_path = resolve_path(root, request.path)
    except PathEscapesRoot:
        # Deliberately indistinguishable from a plain missing file: a 403
        # (or any traversal-specific status) would confirm to an attacker
        # that they'd found the root boundary.
        return Response(status=C.STATUS_NOT_FOUND, headers=[], body=b"")

    try:
        body = read_file(fs_path, C.MAX_PAYLOAD_SIZE)
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return Response(status=C.STATUS_NOT_FOUND, headers=[], body=b"")
    except ValueError:
        return Response(status=C.STATUS_PAYLOAD_TOO_LARGE, headers=[], body=b"")

    headers = [
        ("Content-Type", guess_content_type(fs_path)),
        ("Content-Length", str(len(body))),
    ]
    return Response(status=C.STATUS_OK, headers=headers, body=body)


def _error_response(status: int, message: str) -> Response:
    body = message.encode("utf-8")
    return Response(
        status=status,
        headers=[("Content-Type", "text/plain"), ("Content-Length", str(len(body)))],
        body=body,
    )


def handle_connection(sock: socket.socket, addr, root: str, log=lambda *a: None) -> None:
    log(f"connection from {addr}")
    try:
        while True:
            try:
                frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_REQUEST}))
            except ConnectionClosed:
                log(f"{addr} closed connection")
                return
            except ProtocolError as exc:
                # A malformed *frame header* (bad magic/version/oversized
                # length) means we can no longer trust where the next
                # frame starts. Best-effort tell the client why, then close
                # -- keeping the socket open would risk serving garbage as
                # a "new" frame.
                log(f"{addr} sent malformed frame: {exc}")
                error = _error_response(C.STATUS_BAD_REQUEST, str(exc))
                try:
                    write_frame(
                        sock,
                        C.FRAME_TYPE_RESPONSE,
                        encode_response(error.status, error.headers, error.body),
                    )
                except Exception:
                    pass
                return

            path_for_log = "?"
            try:
                request = decode_request(frame.payload)
                path_for_log = request.path
                response = handle_request(root, request)
            except ProtocolError as exc:
                # The frame itself was read correctly (we consumed exactly
                # payload_length bytes), so the stream is still in sync --
                # this is a semantic error inside one otherwise-valid
                # frame. Reply 400 and keep the connection open.
                log(f"{addr} sent malformed request payload: {exc}")
                response = _error_response(C.STATUS_BAD_REQUEST, str(exc))

            write_frame(
                sock,
                C.FRAME_TYPE_RESPONSE,
                encode_response(response.status, response.headers, response.body),
            )
            log(f"{addr} {path_for_log} -> {response.status}")
    finally:
        sock.close()


class Server:
    def __init__(self, root: str, host: str = "0.0.0.0", port: int = 9000, verbose: bool = False):
        self.root = root
        self.host = host
        self.port = port
        self.verbose = verbose
        self._sock = None

    def _log(self, *args):
        if self.verbose:
            print("[bserve]", *args, file=sys.stderr)

    def serve_forever(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(128)
        self._log(f"listening on {self.host}:{self.port}, root={self.root}")
        try:
            while True:
                conn, addr = self._sock.accept()
                t = threading.Thread(
                    target=handle_connection,
                    args=(conn, addr, self.root, self._log),
                    daemon=True,
                )
                t.start()
        except KeyboardInterrupt:
            self._log("shutting down")
        finally:
            self._sock.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bserve",
        description="Serve files over the binhttp binary protocol.",
    )
    parser.add_argument("root", help="directory to serve files from")
    parser.add_argument("port", type=int, help="TCP port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="address to bind (default 0.0.0.0)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log connections and requests")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    server = Server(root=args.root, host=args.host, port=args.port, verbose=args.verbose)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
