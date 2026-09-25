# Testing

## Running the suite

```bash
./.venv/bin/python3 -m pytest -v          # everything, verbose
./.venv/bin/python3 -m pytest -q          # everything, quiet
./.venv/bin/python3 -m pytest tests/test_fuzz.py -q   # just the fuzz/adversarial suite
```

(`./.venv` is a local virtualenv with `pytest` and `ruff` installed — see
the README's setup step if you haven't created it yet.)

As of this writing: **115 tests, 0 failures**, runtime ~6.6s, stable
across repeated runs (checked 4 consecutive runs with identical results —
no flakiness observed from the threaded server or timing-sensitive
socket tests).

## Suite layout and what each file actually proves

| File | Layer under test | What it proves |
|---|---|---|
| `tests/test_protocol_unit.py` | Pure encode/decode functions, no sockets | Every field round-trips; every documented validation rule (bad magic, bad version, oversized length, truncated fields, header-table bounds) raises `ProtocolError`, not a crash. |
| `tests/test_fragmentation.py` | `read_exact`/`read_raw_frame` against a fake socket (`ChunkedSocket`) that serves bytes in caller-controlled or randomized chunk sizes | Header split across N reads, payload split across N reads, 2-3 frames delivered in a single `recv()`, truncated frame/header, randomized chunk sizes (25 seeded trials) all reconstruct correctly. This is where "never assume one `recv()` returns one frame" is actually exercised, deterministically. |
| `tests/test_unknown_frame_type.py` | `read_known_frame`'s skip logic | An unknown frame type (including one with a 50,000-byte payload) is fully consumed and does not corrupt the next frame's boundary — both against the fake socket and against a real running server mid-connection. |
| `tests/test_persistent_connection.py` | A real `Server` + real sockets | 6+ requests over one connection; responses match requests in order across 20 interleaved hit/miss requests; the same OS file descriptor is reused throughout; a malformed-payload 400 doesn't drop the connection; two independent connections don't cross-talk. |
| `tests/test_file_serving.py` | End-to-end via a running server | Existing/missing/nested/empty/binary files, `Content-Length` correctness, `Content-Type` guessing, `/` serving `index.html`, repeated-request idempotency, `405` for a non-GET method. |
| `tests/test_security_path_traversal.py` | `fsutil.resolve_path` unit-level + end-to-end | 7 traversal payloads (`../`, `../../`, deep traversal, mixed with real segments, literal-dot variants) all stay inside the configured root; a symlink planted inside the root pointing outside is rejected; a null byte in the path is rejected; the server keeps working after all of them. |
| `tests/test_fuzz.py` | Every parsing layer, plus a real live server | ~500-trial random-byte fuzzing of frame headers, header blocks, and request/response payloads; every invalid version (0-255) and reserved header-table id individually checked; a live server is fed 60 random-garbage connections plus a hostile oversized-length-claim header and must still answer a normal request correctly afterward. |
| `tests/test_interop_vectors.py` | Golden byte vectors vs. an independent from-scratch parser | Proves `PROTOCOL.md` is sufficient to reimplement the format without reading `binhttp/` source — see "Interoperability" below. |
| `tests/test_cli.py` | The actual `./bserve`/`./bcurl` executables, via `subprocess` | Exit codes (`0` for success, non-zero for 404/connection-refused/bad-target), stdout is exactly the file's bytes, `-v` hexdump output lands on stderr and includes the magic bytes. |

## TCP fragmentation testing, specifically

`tests/test_fragmentation.py` and `tests/test_fuzz.py` use a hand-rolled
`ChunkedSocket` test double (in `test_fragmentation.py`, imported by the
others) instead of a real socket, specifically *because* real loopback
TCP on a fast local machine tends to deliver whole frames in single
`recv()` calls almost all the time — which would make a bug in the
"assume one `recv()` == one frame" category invisible in CI even though
it's a real bug. `ChunkedSocket.recv(bufsize)` hands back either an
exact caller-specified sequence of chunk sizes, or a `random.Random`
generated size on each call, so the tests can force every fragmentation
edge case (1-byte-at-a-time, N frames in one call, a length crossing a
chunk boundary) deterministically and reproducibly (seeded).

Separately, `tests/test_persistent_connection.py` and
`tests/test_fuzz.py::TestFuzzLiveServer` exercise the *real* socket stack
end-to-end, so both the pure parsing logic and the actual
production code path (`Server.serve_forever` → `handle_connection`) are
covered.

## Fuzz / adversarial testing

`tests/test_fuzz.py` generates, per run: fully random 12-byte headers (500
trials), bit-flipped corruptions of an otherwise-valid header, every
single invalid version byte (0-255 minus the one valid value), every
impossible length value near the boundaries of `uint32`, random header
blocks (500 trials), random request payloads (500 trials), random
response payloads (500 trials), and randomized sequences mixing known and
unknown frame types with random payloads (50 trials of up to 8 frames
each). The single assertion that matters across all of them: **the only
exceptions allowed to propagate are `ProtocolError` and
`ConnectionClosed`** (both handled cleanly by `server.py`/`client.py`) —
anything else (an `IndexError`, `UnicodeDecodeError`, `struct.error`,
etc. escaping uncaught) is a test failure. A separate live-server class in
the same file fires 60 random-garbage TCP connections plus a
hostile-oversized-length header directly at a real `bserve` process and
then confirms it still answers a normal request correctly — proving
survivability isn't just true of the pure functions in isolation.

Total fuzz cases exercised in one run of `test_fuzz.py`, tallied directly
from each test's loop bound (`N_TRIALS=500` × 5 loops, plus the
smaller targeted loops: all 255 invalid versions, all 254 unrecognised
frame types, 245 reserved header-table ids, 100 corrupted-magic and 100
flags/reserved trials, 60 live-server garbage connections, 50 random
frame-sequence trials, plus a handful of single-case boundary checks):
**≈3,570 cases** per run.

Run it repeatedly to build confidence beyond the fixed seeds:

```bash
for i in 1 2 3 4 5; do ./.venv/bin/python3 -m pytest tests/test_fuzz.py -q; done
```

## Interoperability

`tests/test_interop_vectors.py` is the test that actually justifies
calling this a *protocol* rather than a shared implementation trick (the
assignment's own framing: *"A client that only works against your own
server is an implementation, not a protocol"*). It:

1. Defines `independent_parse_frame`/`independent_parse_headers`/
   `independent_parse_request_payload`/`independent_parse_response_payload`
   — small functions written directly against the field layout in
   `PROTOCOL.md`, which import `struct` but **nothing from `binhttp/`**.
2. Parses the golden byte vectors (`tests/fixtures/vectors.py`, the same
   bytes documented byte-by-byte in `HEXDUMP.md`) with that independent
   parser and checks the result against hand-verified expected values.
3. Separately confirms `binhttp.request.decode_request` /
   `binhttp.response.decode_response` agree with the independent parse.
4. Sends the golden **request** frame's raw bytes (not built via
   `encode_request`) over a real socket at a real running `bserve`, and
   confirms it's served correctly.
5. Parses the golden **response** frame's raw bytes as `bcurl` would,
   again without going through `encode_response`.

If our own encoder and the from-scratch parser ever disagreed about what
a given byte sequence means, this is the test that would catch it — round
1-3 above cannot pass by both sides sharing a bug, because round 1's
parser never calls into `binhttp/`.

## Security testing

Covered in `tests/test_security_path_traversal.py`:

- `resolve_path()` unit tests against 7 distinct traversal payloads
  (simple `../`, doubled, 6-levels-deep, traversal mixed with a real
  subdirectory segment, traversal mixed with a literal `.` segment, a
  visually-similar-but-not-actually-`..` payload, and traversal appearing
  after a real segment) — each checked to resolve to somewhere inside the
  configured root, never outside it.
- A dedicated test for the "leading `..` at virtual root collapses
  harmlessly" behavior (documented in `PROTOCOL.md` §5.1) — this is
  intentional, safe behavior, not a bug, and is called out explicitly so a
  future change can't accidentally "fix" it into something less safe.
- A symlink planted inside the served root pointing to a file outside it
  — rejected via `os.path.realpath` resolution (skipped automatically on
  filesystems where symlink creation isn't available in the test
  sandbox).
- A null byte embedded in the path — rejected.
- End-to-end: every traversal payload against a live server returns `404`
  (never leaking the existence or contents of anything outside the root),
  and the server remains fully responsive afterward.

## What is intentionally *not* tested

- **Load/throughput benchmarking.** Out of scope for a correctness- and
  protocol-focused assignment; nothing in the brief asks for performance
  numbers.
- **Pipelining** (client sending `FRAME1 FRAME2 FRAME3` before reading any
  response) end-to-end through `bcurl`, since `bcurl` never does this
  (see `ARCHITECTURE.md`, "What's explicitly out of scope"). The
  *server's* ability to correctly parse several frames arriving in one
  `recv()` **is** tested (`test_fragmentation.py`), since that's the
  server-side mechanism pipelining would need if it existed.
- **IPv6 / non-localhost networking.** All tests bind to `127.0.0.1`;
  nothing in the assignment requires broader network testing.

## Quality gates run before calling this done

```bash
./.venv/bin/python3 -m pytest -q                                   # unit + integration + e2e + fuzz + interop + security
./.venv/bin/python3 -m ruff check binhttp/ tests/ bserve bcurl      # lint
./.venv/bin/python3 -m ruff format --check binhttp/ tests/ bserve bcurl   # formatting
./.venv/bin/python3 -m py_compile binhttp/*.py bserve bcurl         # clean-compile check
```

All four pass with zero errors/warnings as of the last run recorded in
`PROJECT_SCORE.md`.
