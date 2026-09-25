"""Golden protocol test vectors: independently-constructed raw bytes with
their expected decoded meaning. These are the same bytes documented,
byte-by-byte, in HEXDUMP.md.

The point of a golden vector is that it is *not* produced by calling our
own encode_*() functions inside the test that checks it (that would only
prove encode/decode are inverses of each other, not that the spec is
correct or reproducible by an independent implementation). Each byte
sequence below was generated once, reviewed by hand against PROTOCOL.md,
and is now pinned as a literal.
"""

# --- GET /index.html, Host: localhost:9000 ----------------------------------
GOLDEN_REQUEST_FRAME = (
    b"BSC1\x01\x01\x00\x00\x00\x00\x00 \x01\x00\x0b/index.html\x01\x00\x00\x0elocalhost:9000"
)
GOLDEN_REQUEST_EXPECTED = {
    "magic": b"BSC1",
    "version": 1,
    "frame_type": 0x01,  # FRAME_TYPE_REQUEST
    "flags": 0x00,
    "payload_length": 0x20,  # 32
    "method": 0x01,  # METHOD_GET
    "path": "/index.html",
    "headers": [("Host", "localhost:9000")],
}

# --- 200 OK, Content-Type: text/html, Content-Length: 15, body "<html>ok</html>" ---
GOLDEN_RESPONSE_FRAME = (
    b"BSC1\x01\x02\x00\x00\x00\x00\x00'"
    b"\x00\xc8\x02\x01\x00\ttext/html\x02\x00\x0215\x00\x00\x00\x0f<html>ok</html>"
)
GOLDEN_RESPONSE_EXPECTED = {
    "magic": b"BSC1",
    "version": 1,
    "frame_type": 0x02,  # FRAME_TYPE_RESPONSE
    "flags": 0x00,
    "payload_length": 0x27,  # 39
    "status": 200,
    "headers": [("Content-Type", "text/html"), ("Content-Length", "15")],
    "body": b"<html>ok</html>",
}
