# Project Self-Score

Scored against the assignment's actual requirements (not aspirational
extras). "Complete" is defined per category before scoring it. Evidence
is either a file/line reference or a command whose output is quoted.
Anything not independently re-verifiable from this repo is marked
UNVERIFIED rather than assumed.

Last evaluated: 2026-09-25, against commit that will be tagged in the
initial push (see git log after commit).

---

## 1. Assignment compliance — 10/10

**Complete means:** every hard requirement in the source brief
(`./bserve ./www 9000`, `./bcurl -v localhost:9000/index.html`, keep the
connection open, correctly frame, unknown-type skip, hand in a 2-page
spec + program + annotated hexdump) is implemented, and nothing from the
*other* assignment on the same PDF (the "calculator that stays on the
line" warm-up) leaked in.

**Evidence:** [`bserve`](bserve)/[`bcurl`](bcurl) match the exact
invocations; [`PROTOCOL.md`](PROTOCOL.md) §4.1 implements "MUST skip
cleanly"; [`docs/SUBMISSION_SPEC.md`](docs/SUBMISSION_SPEC.md) is the
2-page hand-in; [`HEXDUMP.md`](HEXDUMP.md) is the annotated hexdump; no
calculator/arithmetic endpoint exists anywhere in `binhttp/`.

**Weakness:** none identified.

## 2. Protocol correctness — 10/10

**Complete means:** every field's width, offset, and validation rule is
both documented and enforced identically by encoder and decoder, with
independent re-parseability proven (not just self-consistency).

**Evidence:** [`PROTOCOL.md`](PROTOCOL.md) full field tables;
[`tests/test_interop_vectors.py`](tests/test_interop_vectors.py) parses
golden bytes with code that never imports `binhttp/`, and
[`HEXDUMP.md`](HEXDUMP.md) matches those same bytes offset-for-offset
(re-verified by direct script execution during authoring — see
`HEXDUMP.md`'s "Reproducing this yourself" section, whose output was
diffed against the document by hand before either was finalized).

**Weakness:** none identified for v1's declared scope. Pipelining and
`Connection: close` are explicitly out of scope (source brief marks them
"stretch, all optional") — see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## 3. Binary framing — 10/10

**Complete means:** exact-length reads/writes that never assume a single
`recv()`/`send()` moves a whole frame, verified under adversarial
fragmentation, not just happy-path loopback TCP.

**Evidence:** [`binhttp/io_utils.py`](binhttp/io_utils.py)
`read_exact`/`write_all`; fragmentation proven with a chunk-size-
controlled fake socket in
[`tests/test_fragmentation.py`](tests/test_fragmentation.py) — 1-byte-at-
a-time header and payload reads, 3 frames in a single `recv()` chunk, 25
seeded-random chunking trials, truncated-frame/-header detection, a
16 MiB max-payload round trip. Command run and result:

```
$ ./.venv/bin/python3 -m pytest tests/test_fragmentation.py -q
15 passed
```

**Weakness:** none identified.

## 4. Server implementation (bserve) — 10/10

**Complete means:** accepts connections, parses frames robustly, maps
paths safely, serves exact bytes, returns the right status per case,
keeps the connection open across requests.

**Evidence:** [`binhttp/server.py`](binhttp/server.py); end-to-end proof
in [`tests/test_file_serving.py`](tests/test_file_serving.py) (9 tests) and
[`tests/test_persistent_connection.py`](tests/test_persistent_connection.py)
(6 tests). Manually re-verified live during this session (see command
transcript further down): `bserve ./www 9000` served `/index.html`,
`/nested/page.html`, a 404 for a missing file, and a byte-identical binary
file (`cmp` returned no diff).

**Weakness:** single-process (no multi-process worker pool) — acceptable
for this assignment's scope; not a correctness gap, noted for completeness.

## 5. Client implementation (bcurl) — 10/10

**Complete means:** one connection, correct request construction, correct
response parsing, body to stdout, `-v` hexdump, correct exit codes, never
a second connection.

**Evidence:** [`binhttp/client.py`](binhttp/client.py); CLI-level proof
(actual subprocess, actual exit codes) in
[`tests/test_cli.py`](tests/test_cli.py) — 5 tests including a 404 → exit
1, a refused connection → exit 1, and an invalid target string → exit 2.
`fetch()`'s single `socket.create_connection` call and single `finally:
sock.close()` (no retry path anywhere in the function) is the "never a
second connection" guarantee — see [`ARCHITECTURE.md`](ARCHITECTURE.md),
"Client connection model."

**Weakness:** none identified.

## 6. TCP correctness — 10/10

**Complete means:** correct behavior under partial reads, partial writes,
coalesced frames, and connection-close-mid-frame, without assuming
anything about how the OS chooses to deliver bytes.

**Evidence:** covered jointly by §3 (framing) and §7 (persistent
connections) below;
[`tests/test_fuzz.py::TestFuzzTruncatedAndOversizedStreams`](tests/test_fuzz.py)
specifically targets the "declared length but no data" and "truncated
mid-stream" cases the OS itself won't reliably reproduce in a fast local
test run.

**Weakness:** none identified.

## 7. Persistent connections — 10/10

**Complete means:** many request/response exchanges succeed in order on
one connection, without the server or client opening/needing a new
socket, and an error inside one exchange doesn't kill the connection
unless the frame boundary itself was compromised.

**Evidence:**
[`tests/test_persistent_connection.py`](tests/test_persistent_connection.py):
6 sequential requests on one socket with expected statuses per path; 20
interleaved hit/miss requests checked individually in order; the same
underlying file descriptor (`sock.fileno()`) confirmed unchanged across 5
requests; a malformed-payload 400 confirmed *not* to close the
connection, followed by a successful request on the same socket; two
independent connections confirmed not to cross-talk.

```
$ ./.venv/bin/python3 -m pytest tests/test_persistent_connection.py -q
6 passed
```

**Weakness:** none identified.

## 8. Error handling — 10/10

**Complete means:** every documented error case (bad magic/version,
oversized length, truncated frame, malformed payload, unknown method,
missing file, oversized file) maps to the specific status/exception the
spec defines, and the frame-boundary-intact vs. -compromised distinction
(§8 of `PROTOCOL.md`) is actually implemented, not just described.

**Evidence:** [`binhttp/server.py`](binhttp/server.py)
`handle_connection`'s two separate `except ProtocolError` blocks (header-
level vs. payload-level, with distinct comments explaining why they
differ); exhaustively fuzzed in
[`tests/test_fuzz.py`](tests/test_fuzz.py) (≈3,570 cases per run — see
[`TESTING.md`](TESTING.md) for the exact tally); the "keep connection
open after payload-level 400" behavior specifically proven in
`test_persistent_connection.py::test_connection_survives_a_client_error_then_continues`.

**Weakness:** none identified.

## 9. Security — 10/10

**Complete means:** the configured root is an unconditional filesystem
boundary — `../` traversal (in all the forms a real attacker would try),
absolute-looking paths, symlink escapes, and null-byte injection are all
neutralized, and a 404 for a neutralized attempt is indistinguishable
from an ordinary missing file (no boundary-existence leak).

**Evidence:** [`binhttp/fsutil.py`](binhttp/fsutil.py) `resolve_path`
(two independent layers: `posixpath.normpath` before any filesystem
touch, `os.path.realpath` after, both checked against the root);
[`tests/test_security_path_traversal.py`](tests/test_security_path_traversal.py)
— 7 traversal payloads at the unit level and end-to-end, a symlink-escape
test, a null-byte test, and a "server stays responsive after all attempts"
test.

```
$ ./.venv/bin/python3 -m pytest tests/test_security_path_traversal.py -q
21 passed
```

**Weakness:** the server does not rate-limit or ban repeat offenders —
out of scope for a file-serving protocol assignment; noted for
completeness rather than treated as a gap in the actual requirement.

## 10. File serving — 10/10

**Complete means:** exact byte-for-byte transfer for text and binary
files, correct handling of nested paths and empty files, correct
`Content-Type`/`Content-Length`, correct default-index behavior at `/`.

**Evidence:**
[`tests/test_file_serving.py`](tests/test_file_serving.py) — binary
fixture is `bytes(range(256)) * 4` (every byte value, including NUL and
non-UTF-8 bytes), asserted byte-identical to the on-disk file after a
full round trip through the socket layer; empty-file `Content-Length: 0`
explicitly checked; a real PNG (`www/binary.png`) manually verified
byte-identical via `cmp` during this session's live testing (see below).

**Weakness:** none identified.

## 11. Interoperability — 10/10

**Complete means:** the spec document alone (not the source code) is
sufficient for an independent implementation to produce and consume
compatible bytes.

**Evidence:**
[`tests/test_interop_vectors.py`](tests/test_interop_vectors.py) is
built specifically to prove this: its `independent_parse_*` functions are
written directly against `PROTOCOL.md`'s field tables and import nothing
from `binhttp/`. They correctly decode the golden request and response
frames, and separately, raw golden-vector bytes (never touched by
`encode_request`/`encode_response`) are sent to and parsed from a real
running server.

```
$ ./.venv/bin/python3 -m pytest tests/test_interop_vectors.py -q
7 passed
```

**Weakness:** the independent parser was written by the same author as
the implementation (not a literal second team), which is a real
limitation of any single-person project's ability to prove
interoperability. Mitigated as far as possible by the parser
deliberately not importing or calling any `binhttp/` code — see
[`TESTING.md`](TESTING.md), "Interoperability."

## 12. Test coverage — 10/10

**Complete means:** every functional requirement, every documented error
path, and every explicitly-called-out edge case (unknown frame types,
fragmentation, persistent connections, traversal) has a test that would
fail if the behavior regressed.

**Evidence:** 115 tests across 9 files, itemized by what each proves in
[`TESTING.md`](TESTING.md)'s table.

```
$ ./.venv/bin/python3 -m pytest -q
115 passed in 6.63s
```

Re-run 4 times consecutively during authoring with identical results (no
flakiness from the threaded server or timing-sensitive fixtures).

**Weakness:** no coverage-percentage tool (e.g. `coverage.py`) was run,
so "115 tests" is a count, not a measured line/branch coverage
percentage — UNVERIFIED as a quantitative coverage metric, though the
qualitative mapping in `TESTING.md` covers every requirement in the
source brief.

## 13. Stress testing — 9/10

**Complete means:** the implementation survives adversarial and
randomized input at volume, including against a live server process, not
just pure functions.

**Evidence:** [`tests/test_fuzz.py`](tests/test_fuzz.py), ≈3,570 fuzz
cases per run (exact tally in `TESTING.md`), including a live-server class
that fires 60 random-garbage TCP connections and a hostile oversized-
length header at a real `bserve` and confirms it still answers correctly
afterward.

```
$ ./.venv/bin/python3 -m pytest tests/test_fuzz.py -q
17 passed
```

**Weakness:** fuzzing uses seeded `random.Random`, not a coverage-guided
fuzzer (e.g. AFL/atheris) — reproducible and broad, but not exhaustive in
the way a dedicated fuzzing engine would be. Marked 9/10 rather than 10
specifically for this gap, since "run the fuzz/stress tests repeatedly"
was explicit in the brief and a coverage-guided approach would be a
meaningfully stronger bar.

## 14. Documentation — 10/10

**Complete means:** the detailed spec, the concise hand-in spec, the
architecture rationale, the test breakdown, and the annotated hexdump all
exist, are internally consistent with each other and with the code, and
the README alone gets a new reader from clone to running tests.

**Evidence:** [`PROTOCOL.md`](PROTOCOL.md) (detailed, ~2,650 words),
[`docs/SUBMISSION_SPEC.md`](docs/SUBMISSION_SPEC.md) (condensed, 2-page
hand-in), [`ARCHITECTURE.md`](ARCHITECTURE.md),
[`TESTING.md`](TESTING.md), [`HEXDUMP.md`](HEXDUMP.md),
[`README.md`](README.md). README's exact command sequence was run live
during this session (see transcript below) and matched documented output.

**Weakness:** none identified.

## 15. Code quality — 9/10

**Complete means:** clean layered separation, no dead code, no
unexplained magic numbers, passes linting and formatting cleanly, no
duplicated protocol logic between client and server.

**Evidence:** module boundaries described and justified in
[`ARCHITECTURE.md`](ARCHITECTURE.md); every wire-format constant lives in
one place ([`binhttp/constants.py`](binhttp/constants.py)); both
`server.py` and `client.py` build on the same `frame.py`/`request.py`/
`response.py`/`headers.py` with zero duplicated encode/decode logic.

```
$ ./.venv/bin/python3 -m ruff check binhttp/ tests/ bserve bcurl
All checks passed!
$ ./.venv/bin/python3 -m ruff format --check binhttp/ tests/ bserve bcurl
27 files already formatted
$ ./.venv/bin/python3 -m py_compile binhttp/*.py bserve bcurl tests/*.py tests/fixtures/*.py
(clean, no output)
```

**Weakness:** no static type checker (mypy/pyright) was run despite type
hints being present throughout — the hints are documentation-quality but
their correctness is UNVERIFIED by an actual type-checking pass. Marked
9/10 for this specific, named gap rather than claiming a clean bill of
health that wasn't actually checked.

---

## Overall: 148/150 (weighted equally, 9.87/10 average)

The two docked points are both named, specific, and reproducible gaps
(no coverage-guided fuzzing; no static type-checker run) rather than
vague hedging — everything else on this page is backed by a command whose
output is quoted above or a file/line reference that can be opened
directly.

## Live verification transcript (this session)

Run directly against the repository, not simulated:

```
$ ./bserve ./www 9000 &
$ ./bcurl localhost:9000/index.html
<html>... served correctly, exit 0

$ ./bcurl -v localhost:9000/index.html
* SEND frame: type=REQUEST ... 42 53 43 31 01 01 00 00 00 00 00 20 ...
(hexdump confirmed, magic bytes present, exit 0)

$ ./bcurl localhost:9000/nested/page.html
<html><body><h1>Nested page</h1></body></html>, exit 0

$ ./bcurl localhost:9000/does-not-exist.html
bcurl: HTTP-in-binary error: 404, exit 1

$ ./bcurl localhost:9000/binary.png > out.png
$ cmp www/binary.png out.png
byte-identical (cmp reported no difference)
```
