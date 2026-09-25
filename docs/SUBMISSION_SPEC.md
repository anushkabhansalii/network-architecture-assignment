# binhttp — Submission Specification

*Two pages. Enough for a stranger to implement a compatible client or
server without reading our code. (Full engineering detail, rationale, and
the worked byte example live in `PROTOCOL.md` / `HEXDUMP.md` at the repo
root — this document is the condensed hand-in version.)*

## 1. Transport & connection model

Raw TCP, no TLS. One connection carries any number of request/response
exchanges in strict alternation, client-initiated: client sends a
REQUEST, server sends exactly one RESPONSE, repeat. Either side may close
the connection between exchanges at any time — that's a normal end of
session, not an error. A close in the middle of a frame (fewer bytes than
declared arrive) is treated as an abort. The client opens exactly one
connection per invocation and never opens a second one.

## 2. Frame header (12 bytes, every frame, big-endian)

```
offset  width  field
0       4      magic            ASCII "BSC1"
4       1      version          0x01
5       1      type             0x01=REQUEST, 0x02=RESPONSE (see §5)
6       1      flags            reserved, 0x00 in v1
7       1      reserved         must be 0 on send; receiver ignores it
8       4      payload_length   big-endian uint32, bytes of payload following
```

**Validation, in order:** magic must match exactly; version must be
`0x01`; `payload_length` must not exceed `16,777,216` (16 MiB) — checked
*before* attempting to read that many bytes, so a hostile length can
never force an oversized allocation. Any failure here means the receiver
can no longer trust the stream's byte alignment; it sends a best-effort
`400` and closes the connection. A failure *after* a structurally valid
header (i.e. the payload itself doesn't parse) does **not** require
closing — exactly `payload_length` bytes were already consumed, so the
stream stays aligned; the receiver sends `400` and keeps the connection
open.

## 3. REQUEST payload (type `0x01`)

```
offset  width           field
0       1               method          0x01 = GET (only method in v1)
1       2               path_length     big-endian uint16, max 4096
3       path_length     path            UTF-8, must start with '/'
3+len   var             headers         see §6
```

`path` is always resolved relative to the server's configured root and
can never escape it, regardless of `../` content — see §7.

## 4. RESPONSE payload (type `0x02`)

```
offset      width       field
0           2           status          big-endian uint16: 200/400/404/405/413/500
2           var         headers         see §6
2+hlen      4           body_length     big-endian uint32
6+hlen      body_length body            exact file bytes (empty for non-2xx)
```

## 5. Frame types & the mandatory "skip unknown types" rule

`0x01`=REQUEST, `0x02`=RESPONSE are the only defined types in v1.
`0x03`-`0x7F` reserved for future core types; `0x80`-`0xFF` reserved for
extensions. **A receiver encountering an unrecognised type MUST still
read exactly `payload_length` bytes (using the header it already parsed),
discard them without interpretation, and continue reading the next
frame.** This works precisely because frame *size* is never a function of
frame *type* — the length lives in the one fixed header every frame has,
known or not — which is what lets a future v2 sender introduce new frame
types against existing v1 receivers without corrupting the stream or
requiring a coordinated upgrade. This is not optional behavior; treating
an unknown type as an error is a protocol violation.

## 6. Header list encoding (shared by REQUEST and RESPONSE)

HPACK's first two mechanisms only — a static table for common names, plus
a length-prefixed literal for everything else. No dynamic table, no
Huffman coding.

```
header_count (1 byte), then header_count entries:
  name_id (1 byte)         0x00-0x09 -> static table below; 0xFF -> literal name follows
  [name_len (1) + name]    only present when name_id == 0xFF
  value_len (2, uint16 BE) + value    always present
```

Static table: `0`=Host, `1`=Content-Type, `2`=Content-Length,
`3`=Connection, `4`=Date, `5`=Server, `6`=User-Agent, `7`=Accept,
`8`=Cache-Control, `9`=Last-Modified. Max 64 headers per list.

## 7. Filesystem boundary

The server's configured root is a hard boundary. Implementations must:
normalize `.`/`..` segments before any filesystem access, in a way that
cannot walk above the configured root no matter how many `../` segments
appear (an absolute-path normalize naturally has this property: leading
`..` past the root collapses harmlessly rather than escaping); resolve
symlinks and re-verify the *resolved* location is still inside root
(catches a symlink planted inside root pointing back out); return `404`
for anything that doesn't resolve to a real file inside root — including
neutralized traversal attempts, indistinguishably from an ordinary
missing file, so as not to confirm the root boundary's existence to an
attacker.

## 8. Status codes

`200` OK · `400` Bad Request (malformed frame/payload) · `404` Not Found
(including neutralized traversal) · `405` Method Not Allowed (non-GET)
· `413` Payload Too Large (file exceeds server's servable-size ceiling)
· `500` Internal Server Error.

## 9. Why these specific choices

**12-byte header, 4-byte-aligned fields:** this protocol has no
multiplexing (unlike HTTP/2, whose 24/8/8/31 layout budgets 31 bits for a
stream id it needs and this protocol doesn't), so there's no stream id to
economize bits for. What remains — a self-identifying magic (needed
*because* frames aren't always known-type, so a receiver needs signal to
tell "corrupt stream" apart from "type I don't recognize"), version, type,
flags, reserved, and a 4-byte length — was laid out on 4-byte boundaries
for hexdump/hand-parse readability, since per-frame overhead at
one-frame-per-request volumes is not worth shaving further.

**32-bit `payload_length` instead of HTTP/2's 24-bit:** this protocol
serves arbitrary files, and a 4-byte field keeps every subsequent header
field on a 4-byte-aligned offset (see above); the actual protection
against a hostile length value is the enforced 16 MiB ceiling, not the
field width.

**`reserved` byte, present but unvalidated:** left for a future version to
define without forcing every v1 receiver to be patched first — the
header-level version of §5's "skip what you don't recognize."

## 10. Worked example

A complete `GET /index.html` request (44 bytes) and its `200 OK` response
(51 bytes), annotated byte-by-byte at every offset, are in `HEXDUMP.md` at
the repository root. Both are pinned as golden test vectors
(`tests/fixtures/vectors.py`) and independently re-parsed by code that
never calls into this implementation, in `tests/test_interop_vectors.py`
— the actual proof that this document is sufficient on its own.
