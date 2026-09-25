# Architecture

## Layout

```
binhttp/                  # shared library used by both bserve and bcurl
├── constants.py           # every wire-format constant, in one place
├── errors.py               # ProtocolError, ConnectionClosed
├── io_utils.py              # read_exact(), write_all() -- fragmentation-safe I/O
├── frame.py                  # 12-byte header encode/decode, unknown-type skip logic
├── headers.py                  # header-list encode/decode (static table + literal)
├── request.py                   # REQUEST payload encode/decode
├── response.py                   # RESPONSE payload encode/decode
├── hexdump.py                     # shared hex+ASCII dump formatting
├── fsutil.py                       # safe path resolution + file reading
├── server.py                        # bserve: accept loop, per-connection handler, CLI
└── client.py                         # bcurl: one connection, one request, -v hexdump, CLI

bserve, bcurl               # thin executable entry points (chmod +x, python3 shebang)
www/                         # sample content served by bserve out of the box
tests/                        # pytest suite (see TESTING.md)
PROTOCOL.md, HEXDUMP.md, ...   # documentation
```

## Why this split

Each module owns exactly one layer, and nothing reaches past its
neighbor:

- **`io_utils`** knows nothing about frames. It only knows "read exactly N
  bytes, however many `recv()` calls that takes" and "write this whole
  buffer, however many `send()` calls that takes." Every other module
  builds on top of it instead of calling `sock.recv()`/`sock.send()`
  directly — this is what makes fragmentation handling automatic
  everywhere instead of a special case some call sites remember and
  others forget.

- **`frame.py`** knows the 12-byte header and "payload is
  `payload_length` opaque bytes." It does **not** know what a REQUEST or
  RESPONSE payload looks like inside — that's deliberate, because it's
  also the layer responsible for the "skip unknown frame types" rule
  (§4.1 of `PROTOCOL.md`), which has to work *without* understanding the
  payload.

- **`request.py` / `response.py`** each own one payload format. Neither
  imports the other. `headers.py` is a third, smaller shared format both
  of them delegate to, since the header-list encoding is identical on
  both sides of the connection.

- **`fsutil.py`** is the only module that touches the filesystem. The
  path-traversal defense (`resolve_path`) lives here and nowhere else, so
  there is exactly one place to audit for that class of bug.

- **`server.py` / `client.py`** are the only modules that own a live
  socket and implement the CLI. They compose the layers below rather than
  reimplementing any of them — `server.py` in particular never calls
  `struct.pack`/`unpack` itself; all of that is `request.py`/`response.py`'s
  job.

## Server concurrency model

`bserve` accepts connections in a loop and hands each one to its own
daemon thread (`threading.Thread`, see `Server.serve_forever`). Within a
connection, everything is sequential and blocking: one request frame in,
handle it, one response frame out, repeat. This keeps the protocol layer
free of any concurrency concerns (no shared mutable state between
connections, no locking inside a single connection's request/response
loop) while still letting multiple clients connect at once without one
slow client blocking another. The assignment's protocol itself has no
concurrency inside one connection — request N+1 is never sent before
response N arrives (`bcurl` is strictly one-request-at-a-time; nothing in
`PROTOCOL.md` defines pipelining) — so there is nothing to parallelize
*within* a connection.

## Client connection model

`bcurl.fetch()` opens exactly one `socket.create_connection(...)`, and
every code path — success, a 4xx/5xx response, a malformed response, a
network error — reaches the same `finally: sock.close()`. There is no
retry loop, no fallback host, no second `connect()` call anywhere in
`binhttp/client.py`. This is intentional and tested
(`tests/test_persistent_connection.py`'s equivalents on the client side
are the CLI subprocess tests in `tests/test_cli.py`, which observe the
process's actual exit code and stdout rather than internal state).

## Error-handling boundary (why some errors close the connection and others don't)

This is the one piece of `server.py` that isn't obvious from reading the
function signatures alone, so it's called out both here and in
`PROTOCOL.md` §8:

- A malformed **frame header** (bad magic/version/oversized length) means
  the receiver no longer knows where the next frame would start in the
  byte stream — framing itself has failed. The only safe move is to
  abandon the connection (after a best-effort error response).
- A malformed **payload inside an otherwise-valid frame** doesn't have
  this problem: the reader already consumed exactly `payload_length`
  bytes (that's what "valid frame" means here), so the stream is still
  byte-aligned. The server replies `400` and keeps the connection open for
  the next request.

`handle_connection()` in `server.py` implements exactly this distinction
via two separate `except ProtocolError` blocks — see the comments there.

## What's explicitly out of scope (v1)

- **Pipelining** (`FRAME1 FRAME2 FRAME3` sent back-to-back by the client
  *before* reading any response) is not implemented, because `bcurl`
  never does it and nothing in the assignment requires it as a hard
  requirement (it's listed under "stretch, all optional" in the source
  brief). The frame-level fragmentation tests in
  `tests/test_fragmentation.py` do exercise "multiple frames arrive in one
  `recv()`", which is the server-side half of what pipelining would need —
  see `TESTING.md` for what is and isn't covered.
- **`Connection: close` / idle timeouts** — also called out as optional
  in the source brief; not implemented.
- **Chunked encoding** — also optional; every response's body length is
  known upfront (it's a file already on disk), so there was never a
  reason to stream a body of unknown length.
