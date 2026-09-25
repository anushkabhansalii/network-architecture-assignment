"""bcurl: the client half of the protocol.

    ./bcurl -v localhost:9000/index.html

Opens exactly one TCP connection, sends exactly one REQUEST frame, reads
exactly one RESPONSE frame, writes the body to stdout, and exits. It never
opens a second connection for any reason (no retries, no redirects).
"""

import argparse
import socket
import sys

from . import constants as C
from .errors import ConnectionClosed, ProtocolError
from .frame import encode_frame, read_known_frame, write_frame
from .hexdump import hexdump
from .request import encode_request
from .response import decode_response

_FRAME_TYPE_NAMES = {
    C.FRAME_TYPE_REQUEST: "REQUEST",
    C.FRAME_TYPE_RESPONSE: "RESPONSE",
}


def parse_target(target: str) -> "tuple[str, int, str]":
    """Parse 'host:port/path' (an optional 'scheme://' prefix is ignored,
    and a missing path defaults to '/')."""
    if "://" in target:
        target = target.split("://", 1)[1]

    if "/" in target:
        hostport, _, path_rest = target.partition("/")
        path = "/" + path_rest
    else:
        hostport, path = target, "/"

    if ":" not in hostport:
        raise ValueError(f"target must include a port, e.g. localhost:9000/path (got {target!r})")
    host, _, port_str = hostport.rpartition(":")
    try:
        port = int(port_str)
    except ValueError:
        raise ValueError(f"invalid port in target: {port_str!r}") from None

    return host, port, path


def _log_frame(
    verbose: bool, direction: str, frame_type: int, flags: int, header_bytes: bytes, payload: bytes
) -> None:
    if not verbose:
        return
    type_name = _FRAME_TYPE_NAMES.get(frame_type, f"UNKNOWN(0x{frame_type:02x})")
    print(
        f"* --- {direction} frame: type={type_name} flags=0x{flags:02x} payload_length={len(payload)} ---",
        file=sys.stderr,
    )
    print("* header:", file=sys.stderr)
    print(hexdump(header_bytes), file=sys.stderr)
    if payload:
        print("* payload:", file=sys.stderr)
        print(hexdump(payload), file=sys.stderr)


def fetch(host: str, port: int, path: str, verbose: bool = False) -> int:
    """Perform one GET and write the body to stdout. Returns the process
    exit code (0 for 2xx/3xx, non-zero for 4xx/5xx or a hard failure)."""
    if verbose:
        print(f"* connecting to {host}:{port}", file=sys.stderr)

    try:
        sock = socket.create_connection((host, port), timeout=10)
    except OSError as exc:
        print(f"bcurl: could not connect to {host}:{port}: {exc}", file=sys.stderr)
        return 1

    try:
        request_headers = [("Host", f"{host}:{port}")]
        request_payload = encode_request(C.METHOD_GET, path, request_headers)
        request_frame = encode_frame(C.FRAME_TYPE_REQUEST, request_payload)

        if verbose:
            print(f"> GET {path}", file=sys.stderr)
            _log_frame(
                verbose,
                "SEND",
                C.FRAME_TYPE_REQUEST,
                0,
                request_frame[: C.HEADER_SIZE],
                request_payload,
            )

        write_frame(sock, C.FRAME_TYPE_REQUEST, request_payload)

        try:
            frame = read_known_frame(sock, frozenset({C.FRAME_TYPE_RESPONSE}))
        except ConnectionClosed:
            print("bcurl: server closed the connection without responding", file=sys.stderr)
            return 1
        except ProtocolError as exc:
            print(f"bcurl: malformed response frame: {exc}", file=sys.stderr)
            return 1

        _log_frame(verbose, "RECV", frame.frame_type, frame.flags, frame.header_bytes, frame.payload)

        try:
            response = decode_response(frame.payload)
        except ProtocolError as exc:
            print(f"bcurl: malformed response payload: {exc}", file=sys.stderr)
            return 1

        if verbose:
            print(f"< status {response.status}", file=sys.stderr)
            for name, value in response.headers:
                print(f"< {name}: {value}", file=sys.stderr)

        sys.stdout.buffer.write(response.body)
        sys.stdout.buffer.flush()

        if response.status >= 400:
            print(f"bcurl: HTTP-in-binary error: {response.status}", file=sys.stderr)
            return 1
        return 0
    finally:
        # Exactly one connection, opened once above, closed here. No retry
        # path in this function ever opens another.
        sock.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bcurl",
        description="Fetch a file from a bserve server over the binhttp binary protocol.",
    )
    parser.add_argument("target", help="host:port/path, e.g. localhost:9000/index.html")
    parser.add_argument("-v", "--verbose", action="store_true", help="hexdump every frame sent/received")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        host, port, path = parse_target(args.target)
    except ValueError as exc:
        print(f"bcurl: {exc}", file=sys.stderr)
        return 2
    return fetch(host, port, path, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
