# binhttp — HTTP, in binary. Two tracks, one protocol.

A from-scratch binary request/response protocol over a persistent TCP
connection, with a server (`bserve`) that serves static files and a
client (`bcurl`) that fetches them — nothing shares code except the wire
format itself, which is fully specified in [`PROTOCOL.md`](PROTOCOL.md).

- **Server:** `./bserve <root> <port>` — accepts connections, serves files
  under `<root>` over the binary protocol, keeps each connection open
  across multiple requests.
- **Client:** `./bcurl [-v] host:port/path` — opens one TCP connection,
  sends one request, prints the response body to stdout, exits non-zero
  on a 4xx/5xx status.

No framework, no third-party runtime dependency — just Python's standard
library `socket` module. Requires **Python 3.9+**.

## Quick start

```bash
# 1. set up a virtualenv with the test/lint tooling (only needed to run tests)
python3 -m venv .venv
./.venv/bin/python3 -m pip install -r requirements-dev.txt

# 2. start the server (root directory, then port)
./bserve ./www 9000

# 3. in another terminal, fetch a file
./bcurl localhost:9000/index.html

# 4. same fetch, with every frame hexdumped to stderr
./bcurl -v localhost:9000/index.html
```

`bserve` and `bcurl` are already executable (`chmod +x`) and run directly
with your system's `python3` — the venv above is only needed for the test
suite and linter, not to run the programs themselves.

## Layout

```
bserve, bcurl        executable entry points
binhttp/              the shared protocol/server/client library
www/                   sample content bserve serves by default
tests/                  pytest suite (115 tests — see TESTING.md)
PROTOCOL.md              wire format specification (source of truth)
HEXDUMP.md                byte-by-byte annotated request + response
ARCHITECTURE.md            module layout and design decisions
TESTING.md                  what's tested and how to run it
docs/SUBMISSION_SPEC.md      the concise 2-page hand-in spec
PROJECT_SCORE.md              self-assessment against the assignment
```

## Trying it end-to-end

```bash
./bserve ./www 9000 &
./bcurl -v localhost:9000/index.html        # existing file -> 200, body to stdout
./bcurl localhost:9000/nested/page.html     # nested file -> 200
./bcurl localhost:9000/does-not-exist.html  # missing file -> 404, exit code 1
echo $?                                     # -> 1
./bcurl localhost:9000/binary.png > out.png # binary file, byte-exact
cmp www/binary.png out.png && echo "byte-identical"
kill %1
```

## Running the tests

```bash
./.venv/bin/python3 -m pytest -v
```

115 tests covering protocol unit tests, TCP fragmentation (including
randomized chunking), unknown-frame-type handling, persistent-connection
behavior, file serving, path-traversal security, a ~3,570-case fuzz
harness, golden-vector interoperability, and CLI subprocess exit codes.
Full breakdown in [`TESTING.md`](TESTING.md).

Lint / format check:

```bash
./.venv/bin/python3 -m ruff check binhttp/ tests/ bserve bcurl
./.venv/bin/python3 -m ruff format --check binhttp/ tests/ bserve bcurl
```

## Adding your own content

Drop any file under `www/` (or whatever root you pass to `bserve`) and
request it by path — nested directories work, and `/` serves
`index.html` if present:

```bash
mkdir -p www/docs
echo "hello" > www/docs/note.txt
./bcurl localhost:9000/docs/note.txt
```

## Protocol summary

Every exchange is a 12-byte fixed header (`magic`, `version`, `type`,
`flags`, `reserved`, `payload_length`) followed by a type-specific
payload. Full field-by-field layout, status codes, header encoding, and
the required "skip unknown frame types cleanly" behavior are in
[`PROTOCOL.md`](PROTOCOL.md); a real request and response are dissected
byte-by-byte in [`HEXDUMP.md`](HEXDUMP.md).

## Known limitations

See "What's explicitly out of scope (v1)" in
[`ARCHITECTURE.md`](ARCHITECTURE.md) — pipelining, `Connection: close`
semantics, and chunked encoding are the source assignment's listed
optional stretch goals and are not implemented. Everything in the
assignment's required feature set (not marked "stretch, all optional")
is implemented and tested; see [`PROJECT_SCORE.md`](PROJECT_SCORE.md) for
the itemized self-assessment.
