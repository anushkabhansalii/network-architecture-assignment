"""Safe mapping of a request path onto a file under the server's root.

The configured root is a hard filesystem boundary: nothing in this module
may return a path outside it, however the request path is constructed
(dot-dot segments, an absolute-looking path, or a symlink planted inside
the root that points back out).
"""

import mimetypes
import os
import posixpath


class PathEscapesRoot(Exception):
    """Resolving the request path would leave the configured root."""


def resolve_path(root: str, request_path: str) -> str:
    """Map a request path (always '/'-absolute, UTF-8, protocol-level) onto
    an absolute filesystem path guaranteed to be inside root.

    Two independent defenses are combined, because either one alone is a
    known-incomplete traversal guard:

      1. posixpath.normpath collapses '.', '..', and repeated slashes
         *before* touching the filesystem. On an absolute POSIX path,
         normpath cannot walk above '/': normpath('/../../etc') == '/etc'.
         This stops '../', '../../', etc. from ever reaching os.path.join.

      2. os.path.realpath resolves the *actual* file after step 1, which
         also resolves symlinks. If a symlink inside the root points
         outside it, this catches what step 1 structurally cannot.
    """
    if "\x00" in request_path:
        raise PathEscapesRoot("null byte in path")
    if not request_path.startswith("/"):
        raise PathEscapesRoot("request path must be absolute")

    normalized = posixpath.normpath(request_path)  # e.g. '/../x' -> '/x'
    relative = normalized.lstrip("/")

    root_real = os.path.realpath(root)
    candidate = os.path.join(root_real, relative) if relative else root_real
    candidate_real = os.path.realpath(candidate)

    if candidate_real != root_real and not candidate_real.startswith(root_real + os.sep):
        raise PathEscapesRoot(f"{request_path!r} resolves outside root")

    return candidate_real


def read_file(path: str, max_size: int) -> bytes:
    """Read the full contents of path. Raises FileNotFoundError,
    IsADirectoryError, or ValueError (oversized file) -- all handled by
    the caller and mapped to a protocol status code."""
    if os.path.isdir(path):
        index = os.path.join(path, "index.html")
        if os.path.isfile(index):
            path = index
        else:
            raise IsADirectoryError(path)

    size = os.path.getsize(path)
    if size > max_size:
        raise ValueError(f"file of {size} bytes exceeds max servable size {max_size}")

    with open(path, "rb") as f:
        return f.read()


def guess_content_type(path: str) -> str:
    content_type, _encoding = mimetypes.guess_type(path)
    return content_type or "application/octet-stream"
