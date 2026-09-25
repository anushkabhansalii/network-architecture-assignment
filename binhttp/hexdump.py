"""Readable hex+ASCII dumping for -v mode and documentation generation."""


def hexdump(data: bytes, bytes_per_line: int = 16) -> str:
    """Classic `hexdump -C` style: offset, hex bytes, ASCII gutter."""
    lines = []
    for offset in range(0, len(data), bytes_per_line):
        chunk = data[offset : offset + bytes_per_line]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        hex_part = hex_part.ljust(bytes_per_line * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:08x}  {hex_part}  |{ascii_part}|")
    if not data:
        lines.append(f"{0:08x}  {'(empty)'}")
    return "\n".join(lines)
