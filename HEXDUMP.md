# Annotated Hexdump: One Complete Request and Response

These are real bytes, produced by this implementation
(`binhttp.request.encode_request` / `binhttp.response.encode_response`)
and pinned as golden test vectors in
[`tests/fixtures/vectors.py`](tests/fixtures/vectors.py). They are
independently re-parsed — without calling any `binhttp/` code — by
[`tests/test_interop_vectors.py`](tests/test_interop_vectors.py), which is
what proves this table (and [`PROTOCOL.md`](PROTOCOL.md), which it's
derived from) is actually sufficient to reimplement the protocol from
scratch.

Scenario: `bcurl localhost:9000/index.html` against a `bserve` whose
`www/index.html` contains `<html>ok</html>` (15 bytes).

## Request frame — `GET /index.html`, `Host: localhost:9000`

Raw bytes (44 total):

```
00000000  42 53 43 31 01 01 00 00 00 00 00 20 01 00 0b 2f  |BSC1....... .../|
00000010  69 6e 64 65 78 2e 68 74 6d 6c 01 00 00 0e 6c 6f  |index.html....lo|
00000020  63 61 6c 68 6f 73 74 3a 39 30 30 30              |calhost:9000|
```

| Offset (hex) | Bytes                         | Field                | Meaning                                              |
|-------------:|--------------------------------|-----------------------|-------------------------------------------------------|
| `00`         | `42 53 43 31`                  | `magic`               | ASCII `"BSC1"`                                         |
| `04`         | `01`                            | `version`              | `1`                                                    |
| `05`         | `01`                            | `type`                 | `0x01` = `REQUEST`                                     |
| `06`         | `00`                            | `flags`                | none set                                               |
| `07`         | `00`                            | `reserved`             | must be `0`                                            |
| `08`         | `00 00 00 20`                   | `payload_length`       | `32` bytes follow                                      |
| `0C`         | `01`                            | `method`               | `0x01` = `GET`                                         |
| `0D`         | `00 0b`                         | `path_length`          | `11` bytes                                             |
| `0F`         | `2f 69 6e 64 65 78 2e 68 74 6d 6c` | `path`              | `"/index.html"` (11 bytes)                              |
| `1A`         | `01`                            | `header_count`         | `1` header                                             |
| `1B`         | `00`                            | header[0] `name_id`    | `0x00` = `Host` (static table)                          |
| `1C`         | `00 0e`                         | header[0] `value_len`  | `14` bytes                                             |
| `1E`         | `6c 6f 63 61 6c 68 6f 73 74 3a 39 30 30 30` | header[0] `value` | `"localhost:9000"` (14 bytes)                    |

Total: 12-byte header + 32-byte payload = **44 bytes**, ending at offset
`0x2B` inclusive.

## Response frame — `200 OK`, body `<html>ok</html>`

Raw bytes (51 total):

```
00000000  42 53 43 31 01 02 00 00 00 00 00 27 00 c8 02 01  |BSC1.......'....|
00000010  00 09 74 65 78 74 2f 68 74 6d 6c 02 00 02 31 35  |..text/html...15|
00000020  00 00 00 0f 3c 68 74 6d 6c 3e 6f 6b 3c 2f 68 74  |....<html>ok</ht|
00000030  6d 6c 3e                                         |ml>|
```

| Offset (hex) | Bytes                        | Field                 | Meaning                                    |
|-------------:|-------------------------------|-------------------------|----------------------------------------------|
| `00`         | `42 53 43 31`                 | `magic`                  | ASCII `"BSC1"`                                |
| `04`         | `01`                           | `version`                 | `1`                                           |
| `05`         | `02`                           | `type`                    | `0x02` = `RESPONSE`                           |
| `06`         | `00`                           | `flags`                   | none set                                      |
| `07`         | `00`                           | `reserved`                | must be `0`                                   |
| `08`         | `00 00 00 27`                  | `payload_length`          | `39` bytes follow                             |
| `0C`         | `00 c8`                        | `status`                  | `200` (`0x00c8`)                               |
| `0E`         | `02`                           | `header_count`             | `2` headers                                   |
| `0F`         | `01`                           | header[0] `name_id`        | `0x01` = `Content-Type`                        |
| `10`         | `00 09`                        | header[0] `value_len`      | `9` bytes                                     |
| `12`         | `74 65 78 74 2f 68 74 6d 6c`    | header[0] `value`          | `"text/html"` (9 bytes)                        |
| `1B`         | `02`                           | header[1] `name_id`        | `0x02` = `Content-Length`                      |
| `1C`         | `00 02`                        | header[1] `value_len`      | `2` bytes                                     |
| `1E`         | `31 35`                        | header[1] `value`          | ASCII `"15"` (2 bytes)                         |
| `20`         | `00 00 00 0f`                  | `body_length`              | `15` bytes                                    |
| `24`         | `3c 68 74 6d 6c 3e 6f 6b 3c 2f 68 74 6d 6c 3e` | `body` | `"<html>ok</html>"` (15 bytes, the exact file contents) |

Total: 12-byte header + 39-byte payload = **51 bytes**, ending at offset
`0x32` inclusive.

## Reproducing this yourself

```bash
./.venv/bin/python3 -m pytest tests/test_interop_vectors.py -v
```

Or regenerate the raw bytes directly:

```bash
./.venv/bin/python3 - <<'PY'
from binhttp import constants as C
from binhttp.frame import encode_frame
from binhttp.request import encode_request
from binhttp.response import encode_response
from binhttp.hexdump import hexdump

req = encode_frame(C.FRAME_TYPE_REQUEST,
                    encode_request(C.METHOD_GET, "/index.html", [("Host", "localhost:9000")]))
print(hexdump(req))

body = b"<html>ok</html>"
resp = encode_frame(C.FRAME_TYPE_RESPONSE,
                     encode_response(200, [("Content-Type", "text/html"),
                                            ("Content-Length", str(len(body)))], body))
print(hexdump(resp))
PY
```
