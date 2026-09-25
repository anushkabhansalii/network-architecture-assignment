"""Wire-format constants for the binhttp protocol.

Every value here is defended in PROTOCOL.md. Nothing in the client or
server should hardcode a magic number that isn't defined here.
"""

import struct

# ---------------------------------------------------------------------------
# Fixed frame header: 12 bytes, big-endian ("network byte order"), 4-byte
# aligned so the trailing length field starts on a word boundary.
#
#   +--------+--------+--------+--------+
#   |          magic (32)              |
#   +--------+--------+--------+--------+
#   |version |  type  | flags  |reserved|
#   +--------+--------+--------+--------+
#   |          payload_length (32)     |
#   +--------+--------+--------+--------+
# ---------------------------------------------------------------------------

MAGIC = b"BSC1"  # "Binary Serve/Curl", format revision 1
VERSION = 1

HEADER_STRUCT = struct.Struct(">4sBBBBI")
HEADER_SIZE = HEADER_STRUCT.size  # 12 bytes
assert HEADER_SIZE == 12

# --- Frame types ------------------------------------------------------------
# 0x00 is deliberately invalid (an all-zero frame, e.g. from a truncated
# read or a zeroed buffer, must never be mistaken for a legitimate frame).
FRAME_TYPE_REQUEST = 0x01
FRAME_TYPE_RESPONSE = 0x02
# 0x03-0x7F reserved for future core frame types (version 2+).
# 0x80-0xFF reserved for extension/experimental frame types. A conforming
# receiver MUST skip any frame type it does not recognise -- see
# read_and_skip_unknown_frame() in binhttp/frame.py.
KNOWN_FRAME_TYPES = {FRAME_TYPE_REQUEST, FRAME_TYPE_RESPONSE}

# --- Limits ------------------------------------------------------------------
# 16 MiB mirrors HTTP/2's 24-bit default frame size ceiling: generous enough
# for real files, small enough that a malicious length field can't be used
# to force a multi-gigabyte allocation before a single byte is validated.
MAX_PAYLOAD_SIZE = 16 * 1024 * 1024
MAX_PATH_LENGTH = 4096
MAX_HEADER_COUNT = 64
MAX_HEADER_NAME_LENGTH = 255
MAX_HEADER_VALUE_LENGTH = 65535

# --- Methods (request payload) ----------------------------------------------
METHOD_GET = 0x01
METHOD_NAMES = {METHOD_GET: "GET"}

# --- Status codes (response payload) ----------------------------------------
STATUS_OK = 200
STATUS_BAD_REQUEST = 400
STATUS_NOT_FOUND = 404
STATUS_METHOD_NOT_ALLOWED = 405
STATUS_PAYLOAD_TOO_LARGE = 413
STATUS_INTERNAL_ERROR = 500

STATUS_REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Payload Too Large",
    500: "Internal Server Error",
}

# --- Header name table --------------------------------------------------------
# "Number the ten names you actually send, length-prefix the rest."
# A 1-byte id in [0, 9] refers to one of these well-known header names.
# Id 0xFF signals "custom name follows" (length-prefixed, see headers.py).
# Ids 10-254 are reserved for a future revision of this table.
HEADER_TABLE = [
    "Host",  # 0
    "Content-Type",  # 1
    "Content-Length",  # 2
    "Connection",  # 3
    "Date",  # 4
    "Server",  # 5
    "User-Agent",  # 6
    "Accept",  # 7
    "Cache-Control",  # 8
    "Last-Modified",  # 9
]
HEADER_NAME_TO_ID = {name.lower(): i for i, name in enumerate(HEADER_TABLE)}
HEADER_CUSTOM_MARKER = 0xFF
