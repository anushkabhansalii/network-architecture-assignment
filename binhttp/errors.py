"""Exception types shared across the protocol, server, and client."""


class ProtocolError(Exception):
    """The peer sent bytes that don't form a valid frame.

    Raised for a bad magic, unsupported version, invalid length, or any
    other structurally malformed input. The server maps this to a 400
    response; the client maps it to a hard failure and a non-zero exit.
    """


class ConnectionClosed(Exception):
    """The peer closed the TCP connection (clean EOF mid-read)."""
