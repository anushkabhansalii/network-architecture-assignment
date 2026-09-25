"""Exact-length, fragmentation-safe reads and writes over a raw socket.

A TCP socket's recv()/send() are byte-stream primitives: a single call can
return fewer bytes than requested (short read), and there is no guarantee
that one send() on one end shows up as one recv() on the other. Every other
module in this package builds frames on top of read_exact()/write_all()
rather than touching sock.recv()/sock.send() directly, so that framing
logic never has to think about partial I/O.
"""

import socket

from .errors import ConnectionClosed


def read_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from sock, blocking across as many recv() calls
    as needed. Raises ConnectionClosed if the peer closes before n bytes
    arrive (including immediately, with zero bytes read)."""
    if n == 0:
        return b""
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(min(remaining, 65536))
        if not chunk:
            raise ConnectionClosed(f"peer closed connection after {n - remaining}/{n} bytes")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def write_all(sock: socket.socket, data: bytes) -> None:
    """Write every byte of data, looping across send() as needed since a
    single send() is not guaranteed to accept the whole buffer."""
    view = memoryview(data)
    total = len(view)
    sent = 0
    while sent < total:
        n = sock.send(view[sent:])
        if n == 0:
            raise ConnectionClosed("peer closed connection during write")
        sent += n
