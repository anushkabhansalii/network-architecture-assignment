# binhttp Protocol Specification (v1)

**"HTTP, in binary. Two tracks, one protocol."**

This document specifies the wire format used by `bserve` (server) and
`bcurl` (client) to exchange requests and responses over a single,
persistent TCP connection. It is written to be independently
implementable: everything a second team would need to write their own
client or server against this spec, without reading our source code, is
here.

---

## 1. Overview

- **Transport:** raw TCP. No TLS, no HTTP, no text framing.
- **Connection model:** one TCP connection carries any number of
  request/response exchanges, in strict alternation, initiated by the
  client. The server never sends a frame the client didn't ask for.
- **Unit of exchange:** a *frame* — a fixed 12-byte header followed by a
  variable-length payload whose size the header declares.
- **Byte order:** all multi-byte integer fields are **big-endian**
  ("network byte order"), matching the convention nearly every wire
  protocol (TCP/IP itself, HTTP/2, TLS) already uses, so implementers
  don't have to think about it twice.

## 2. Connection Lifecycle

1. Client opens **one** TCP connection to the server (`connect()`), and
   never opens a second one for any reason — no retries, no redirects, no
   parallel fetches. This is a protocol invariant, not an optimization.
2. Client sends a REQUEST frame.
3. Server sends exactly one RESPONSE frame in reply.
4. Steps 2–3 repeat, any number of times, on the same connection.
5. Either side may close the TCP connection at any time. A close between
   frames is a normal, clean end of the session — not an error. A close
   *mid-frame* (fewer than the declared number of bytes arrive) is treated
   as the peer aborting: the reader raises and gives up on that frame.

There is no explicit "close" frame type in v1: TCP's own FIN is the
lifecycle's only termination signal.

## 3. Frame Header (12 bytes, fixed, present on every frame)

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                        magic (32 bits)                       |
+---------------+---------------+---------------+---------------+
|   version(8)  |    type(8)    |   flags(8)    |  reserved(8)  |
+---------------+---------------+---------------+---------------+
|                    payload_length (32 bits)                  |
+---------------------------------------------------------------+
|                     payload (payload_length bytes)            |
|                              ...                               |
+---------------------------------------------------------------+
```

| Field           | Offset | Width | Meaning                                                                 |
|-----------------|-------:|------:|--------------------------------------------------------------------------|
| `magic`         | 0      | 4     | ASCII `"BSC1"` (`0x42 0x53 0x43 0x31`). Identifies this protocol/revision.|
| `version`       | 4      | 1     | Wire format version. `0x01` for this spec.                              |
| `type`          | 5      | 1     | Frame type. See §4.                                                      |
| `flags`         | 6      | 1     | Bitfield, reserved for per-type flags. `0x00` in v1 (unused).            |
| `reserved`      | 7      | 1     | Must be `0x00` on send. A receiver MUST NOT reject a frame for a nonzero value here — it exists so a future version can use it without breaking v1 parsers that (correctly) ignore it. |
| `payload_length`| 8      | 4     | Number of bytes in the payload that follows, **not including** these 12 header bytes. |

**Total header size: 12 bytes.** This is a deliberate, defended choice
(see §9 "Design rationale").

### 3.1 Header validation (applies to every frame, of every type)

A receiver reads exactly 12 bytes, then:

1. `magic` must equal `"BSC1"` exactly, or the frame is rejected.
2. `version` must equal `0x01`, or the frame is rejected (a future v2
   receiver talking to a v1 sender would reject here too, which is
   correct — this spec makes no claim about cross-version compatibility
   beyond what §7 describes for *frame types*, not versions).
3. `payload_length` must not exceed `MAX_PAYLOAD_SIZE` = **16,777,216
   bytes (16 MiB)**, or the frame is rejected *before* any attempt to read
   that many payload bytes. This bound exists specifically so a hostile or
   corrupted length field can never be used to force an oversized
   allocation or an indefinite read.
4. If all three checks pass, the receiver reads exactly `payload_length`
   more bytes. If the connection closes before all of them arrive, this is
   a protocol-level failure (the peer aborted mid-frame), not "zero more
   frames" — see §2.

A frame that fails validation at steps 1–3 puts the *header itself* in
doubt, which means the receiver can no longer trust where the *next*
frame would start in the byte stream. Framing has failed, and the only
safe response is to abandon the connection (after, if practical, telling
the peer why — see §8).

A frame that fails validation *after* the header (i.e. the header was
fine, exactly `payload_length` bytes were read, but those bytes don't
parse as a legal REQUEST/RESPONSE body) is a different, recoverable case:
the stream is still byte-aligned, because the reader consumed precisely
the number of bytes the header promised. The connection can stay open.

## 4. Frame Types

| Value  | Name       | Sent by | Meaning                                  |
|-------:|------------|---------|-------------------------------------------|
| `0x00` | *(invalid)*| —       | Never a legal type. Reserved so an all-zero buffer (e.g. an uninitialized/truncated read) can never be mistaken for a real frame. |
| `0x01` | `REQUEST`  | client  | One request for one resource. See §5.    |
| `0x02` | `RESPONSE` | server  | One reply to one request. See §6.        |
| `0x03`–`0x7F` | *(reserved)* | — | Reserved for future **core** frame types in a later protocol revision. |
| `0x80`–`0xFF` | *(reserved)* | — | Reserved for **experimental/extension** frame types. |

### 4.1 Unknown frame types — MANDATORY behavior

> A receiver encountering a frame type it does not know **MUST** skip it
> cleanly.

Concretely: the receiver still performs full header validation (§3.1) and
still reads exactly `payload_length` bytes of payload — it just does not
attempt to interpret those bytes as anything, and does not surface the
frame to application logic. It then goes back to reading the *next*
frame's header, exactly as if nothing had happened.

This works **only because** `payload_length` is read from the fixed
header before the type is even inspected. The number of bytes belonging
to a frame is never a function of its type, which is precisely what lets a
frame type invented in a hypothetical "v2" pass safely through a v1
receiver: the v1 receiver doesn't need to understand the payload to know
its size and correctly step over it. This is the mechanism that "leaves
room for a version 2" — a v2 sender can start putting new frame types on
the wire against v1 servers today, and those servers won't choke, corrupt
their stream position, or crash; they'll just ignore what they don't
understand.

An unknown frame type is **not** a parse error and **must not** produce a
400/error response.

## 5. REQUEST payload (frame type `0x01`)

```
 0               1               2               3
+---------------+---------------+---------------+---------------+
|    method(8)  |         path_length (16)       |   path...     |
+---------------+---------------+---------------+---------------+
|                      path (path_length bytes)                 |
+-----------------------------------------------------------------+
|                    headers (see §7)                            |
+-----------------------------------------------------------------+
```

| Field         | Width          | Meaning                                                    |
|---------------|---------------:|--------------------------------------------------------------|
| `method`      | 1 byte         | `0x01` = `GET`. No other method is defined in v1.            |
| `path_length` | 2 bytes        | Length of `path` in bytes, big-endian. Max `4096`.            |
| `path`        | `path_length`  | UTF-8. **Must** start with `/`. Root-relative — see §5.1.      |
| `headers`     | variable       | Header list, format in §7.                                    |

### 5.1 Path semantics and the filesystem boundary

`path` is always interpreted relative to the server's configured root
directory (the second argument to `bserve`), never the real filesystem
root, regardless of how many `/` or `..` segments it contains. A
conforming server:

1. Rejects a `path` that doesn't start with `/` (malformed request → 400).
2. Normalizes `.`/`..` segments *before* touching the filesystem, in a way
   that cannot walk above the configured root even if `path` contains more
   `..` segments than there are directory levels below the root (e.g.
   `/../../etc/passwd` must resolve no higher than the root itself, not to
   the real `/etc/passwd`).
3. Resolves symlinks and re-checks the *resolved* location is still inside
   the root, since a symlink planted inside the root could otherwise point
   back out.
4. If the resolved file doesn't exist (whether because it genuinely
   doesn't, or because a traversal attempt was neutralized into pointing
   at a nonexistent in-root path), returns `404`. The server does **not**
   distinguish "not found" from "traversal attempt neutralized" in its
   response — doing so would leak information about the root boundary to
   an attacker.

## 6. RESPONSE payload (frame type `0x02`)

```
 0               1
+---------------+---------------+
|         status (16)           |
+---------------------------------------------------------------+
|                    headers (see §7)                            |
+---------------------------------------------------------------+
|                    body_length (32)                            |
+---------------------------------------------------------------+
|                  body (body_length bytes)                      |
+-----------------------------------------------------------------+
```

| Field         | Width          | Meaning                                             |
|---------------|---------------:|--------------------------------------------------------|
| `status`      | 2 bytes        | See §6.1.                                               |
| `headers`     | variable       | Header list, format in §7.                              |
| `body_length` | 4 bytes        | Length of `body` in bytes, big-endian.                   |
| `body`        | `body_length`  | The exact requested file's bytes (unmodified — no text-mode translation, no added/removed bytes), or empty for a non-2xx status. |

### 6.1 Status codes (v1)

| Code  | Meaning              | When the server sends it                                          |
|------:|----------------------|---------------------------------------------------------------------|
| `200` | OK                   | The file was found and its bytes are in `body`.                     |
| `400` | Bad Request          | The request frame was malformed (bad path, corrupt header block, ...).|
| `404` | Not Found            | The resolved path doesn't exist under the root (including neutralized traversal attempts). |
| `405` | Method Not Allowed   | `method` was not `0x01` (`GET`).                                     |
| `413` | Payload Too Large    | The requested file exceeds the server's servable size ceiling.       |
| `500` | Internal Server Error| An unexpected server-side failure unrelated to the request's validity.|

## 7. Header list encoding (shared by REQUEST and RESPONSE)

The assignment's brief: *"number the ten names you actually send,
length-prefix the rest — HPACK's first two mechanisms, in an evening."*
This is a deliberately small subset of HPACK's ideas:

- HPACK's **static table** → here, a fixed 10-entry table of common
  header names, referenced by a 1-byte index instead of spelling the name
  out every time.
- HPACK's **literal representation** → here, any header name not in the
  table is sent as an explicit length + UTF-8 bytes.

There is no dynamic table and no Huffman coding in v1 — those are HPACK
mechanisms 3 and 4, and out of scope.

```
+---------------+
| header_count  |   1 byte
+---------------+
| entry[0]      |
| entry[1]      |
|     ...       |
| entry[N-1]    |
+---------------+
```

Each `entry`:

```
+---------------+-----------------+------------------+---------------+-----------------+
|   name_id(8)  | [name_len(8)]   |  [name bytes]     | value_len(16) |  value bytes     |
+---------------+-----------------+------------------+---------------+-----------------+
```

- `name_id`: `0x00`–`0x09` selects a name from the static table below.
  `0xFF` means "a literal name follows" (`name_len` + `name` are present).
  Any other value (`0x0A`–`0xFE`) is invalid in v1 and rejected.
- `name_len` / `name`: **only present** when `name_id == 0xFF`. 1-byte
  length (max 255) + that many UTF-8 bytes.
- `value_len` / `value`: always present. 2-byte big-endian length (max
  65535) + that many UTF-8 bytes.

`header_count` is capped at 64 entries.

### 7.1 Static header name table (v1)

| id (`name_id`) | Name             |
|----------------:|------------------|
| `0x00`          | `Host`           |
| `0x01`          | `Content-Type`   |
| `0x02`          | `Content-Length` |
| `0x03`          | `Connection`     |
| `0x04`          | `Date`           |
| `0x05`          | `Server`         |
| `0x06`          | `User-Agent`     |
| `0x07`          | `Accept`         |
| `0x08`          | `Cache-Control`  |
| `0x09`          | `Last-Modified`  |
| `0x0A`–`0xFE`   | *(reserved for a future table revision)* |
| `0xFF`          | *(literal name follows)*                 |

These ten were picked because they're the names this protocol's own
client and server actually exchange in practice (`Host` on every
request; `Content-Type`/`Content-Length` on every 200 response). Anything
else — a custom debug header, a future extension — still works, just at
the cost of a few extra bytes for the literal name.

## 8. Error handling summary

| Failure                                              | Stream still in sync? | Server behavior                     | Client behavior                        |
|-------------------------------------------------------|:---:|----------------------------------------|-----------------------------------------|
| Bad `magic` / `version` / oversized `payload_length`   | No  | Best-effort send 400, then close connection | Report error, exit non-zero            |
| Well-formed frame, malformed REQUEST/RESPONSE payload  | Yes | Send 400, **keep connection open**      | Report error, exit non-zero            |
| Unknown frame type                                     | Yes | Skip silently (§4.1), keep reading      | Skip silently (§4.1), keep reading      |
| Peer closes mid-frame (fewer than declared bytes)      | No  | Drop the connection                     | Report "connection closed", exit non-zero |
| Peer closes cleanly between frames                     | Yes (session over) | Clean end of session         | Clean end of session (if expected) or exit non-zero (if awaiting a response) |

## 9. Design rationale (the "why" questions the assignment asks)

**Why a 12-byte fixed header, with these specific widths?**
HTTP/2's frame header is 24/8/8/31 bits (length/type/flags/stream-id) — 9
bytes, chosen to keep per-frame overhead low across *millions* of frames
multiplexed over one connection, with a 31-bit stream id because HTTP/2
needs to interleave many logical streams on one TCP connection. This
protocol has no multiplexing (§2: strict request/response alternation,
one exchange at a time), so there's no stream id to budget bits for at
all. What's left — magic, version, type, a length — is laid out on 4-byte
boundaries (12 bytes = three 32-bit words) purely for readability when
someone is looking at a hexdump or writing a parser by hand; at the
volumes this protocol handles (one frame per request, not millions per
second), 3 extra bytes of header versus HTTP/2's 9 is not a cost worth
optimizing away. A 4-byte magic (vs. HTTP/2's implicit "no magic, just a
preface string once") makes every single frame self-identifying, which
matters more here because frames aren't always known-type (§4.1) — a
receiver needs enough signal to tell "corrupt stream" apart from
"frame type I don't recognize," and a magic-per-frame is what makes the
distinction in §8's error table possible at all.

**Why a 4-byte (32-bit) `payload_length` instead of something smaller?**
`Content-Length: 2` values need to be spellable, and this protocol serves
arbitrary files, not just documents comparable to `index.html`. A 3-byte
(24-bit, HTTP/2-style) length field caps a single frame at 16 MiB anyway
(§3.1's `MAX_PAYLOAD_SIZE`), so nothing is gained by shrinking the field
further, and a 4-byte field keeps every header field's start offset
4-byte-aligned (see previous answer), which the 24-bit choice would break.
The enforced *ceiling* (`MAX_PAYLOAD_SIZE`), not the field width, is what
actually protects the receiver from a hostile length value — see §3.1.

**Why is `reserved` present but not validated?**
So a future version's receiver can use that byte for something (say, a
sub-type or a compression flag) without every v1 receiver in the wild
needing to be patched first — a v1 receiver that already ignores
`reserved` won't reject a v2 sender's frames just because that byte is
non-zero. This is the header-level analogue of §4.1's "skip unknown frame
types": both are places where this spec deliberately leaves the door open
for its own future revision, per the assignment's explicit callout: *"That
is how you leave room for a version 2."*

**Why length-prefix custom headers instead of always spelling out names?**
Two extremes were available: always send full ASCII header names (simple,
but pays 10+ bytes of `Content-Type` on every single response that has
one), or build the full HPACK dynamic-table + Huffman machinery (maximally
compact, but far more machinery than a from-scratch teaching project
needs). The 10-entry static table + literal fallback is the smallest
version of "don't pay for what you don't need" that still generalizes to
an arbitrary header the client or server wants to send later.

## 10. Worked example

See [`HEXDUMP.md`](HEXDUMP.md) for a complete, byte-by-byte annotated
request and response, generated directly from this implementation's own
`encode_request`/`encode_response` and independently re-parsed in
[`tests/test_interop_vectors.py`](tests/test_interop_vectors.py) by a
second, from-scratch parser that never calls into `binhttp/` — proof that
this document, not the code, is the actual source of truth.
