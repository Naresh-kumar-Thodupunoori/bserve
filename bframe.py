"""Frame and header-block encoding for BHTTP/2. See SPEC.md.

The frame layer copies HTTP/2 (RFC 9113 section 4.1) so the bit layout
is 24/8/8/1+31. The header block is the stateless subset of HPACK
(RFC 7541): static table only, no Huffman, no dynamic table.
"""
import struct

HEADER_SIZE = 9
MAX_PAYLOAD = 16384  # HTTP/2's default SETTINGS_MAX_FRAME_SIZE

TYPE_DATA = 0x0
TYPE_HEADERS = 0x1

FLAG_END_STREAM = 0x01
FLAG_END_HEADERS = 0x04

# HPACK static table, RFC 7541 appendix A. 1-based on the wire: entry i
# lives at STATIC_TABLE[i-1]. value None means the table has no value for
# that name, so it can never be sent as a one-byte indexed field.
STATIC_TABLE = [
    (":authority", None), (":method", "GET"), (":method", "POST"),
    (":path", "/"), (":path", "/index.html"), (":scheme", "http"),
    (":scheme", "https"), (":status", "200"), (":status", "204"),
    (":status", "206"), (":status", "304"), (":status", "400"),
    (":status", "404"), (":status", "500"), ("accept-charset", None),
    ("accept-encoding", "gzip, deflate"), ("accept-language", None),
    ("accept-ranges", None), ("accept", None),
    ("access-control-allow-origin", None), ("age", None), ("allow", None),
    ("authorization", None), ("cache-control", None),
    ("content-disposition", None), ("content-encoding", None),
    ("content-language", None), ("content-length", None),
    ("content-location", None), ("content-range", None),
    ("content-type", None), ("cookie", None), ("date", None),
    ("etag", None), ("expect", None), ("expires", None), ("from", None),
    ("host", None), ("if-match", None), ("if-modified-since", None),
    ("if-none-match", None), ("if-range", None),
    ("if-unmodified-since", None), ("last-modified", None),
    ("link", None), ("location", None), ("max-forwards", None),
    ("proxy-authenticate", None), ("proxy-authorization", None),
    ("range", None), ("referer", None), ("refresh", None),
    ("retry-after", None), ("server", None), ("set-cookie", None),
    ("strict-transport-security", None), ("transfer-encoding", None),
    ("user-agent", None), ("vary", None), ("via", None),
    ("www-authenticate", None),
]

# exact (name, value) -> table index, for the indexed opcode
_INDEXED = {}
for _i, (_name, _value) in enumerate(STATIC_TABLE):
    _INDEXED[(_name, _value)] = _i + 1

# type, flags, stream id — the 6 bytes after the 3-byte length. '!' pins
# the fields to network byte order so nothing depends on the host.
_rest = struct.Struct("!BBI")


class FrameError(Exception):
    pass


def pack_frame(ftype, flags, stream_id, payload):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too big for one frame")
    if not 0 <= stream_id <= 0x7FFFFFFF:
        raise ValueError("stream id does not fit in 31 bits")
    length = len(payload).to_bytes(3, "big")
    return length + _rest.pack(ftype, flags, stream_id) + payload


def parse_frame_header(buf):
    """buf is exactly HEADER_SIZE bytes. The reserved bit is masked off,
    as RFC 9113 says receivers must ignore it."""
    length = int.from_bytes(buf[:3], "big")
    ftype, flags, sid = _rest.unpack(buf[3:])
    if length > MAX_PAYLOAD:
        raise FrameError("frame length %d over limit" % length)
    return length, ftype, flags, sid & 0x7FFFFFFF


def read_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data


def read_frame(sock):
    """One full frame as (ftype, flags, stream_id, payload).

    Returns None on a clean close at a frame boundary. Anything that
    arrives short raises FrameError and the caller can give up.
    """
    head = read_exact(sock, HEADER_SIZE)
    if not head:
        return None
    if len(head) < HEADER_SIZE:
        raise FrameError("connection died mid header")
    length, ftype, flags, stream_id = parse_frame_header(head)
    payload = read_exact(sock, length)
    if len(payload) < length:
        raise FrameError("connection died mid payload")
    return ftype, flags, stream_id, payload


def encode_headers(pairs):
    out = bytearray()
    for name, value in pairs:
        n = name.encode("utf-8")
        v = value.encode("utf-8")
        if len(n) > 255 or len(v) > 255:
            raise FrameError("header name or value over 255 bytes")
        idx = _INDEXED.get((name, value))
        if idx is not None:
            out.append(0x80 | idx)
        else:
            out.append(0x00)
            out.append(len(n))
            out += n
            out.append(len(v))
            out += v
    return bytes(out)


def decode_headers(payload):
    """Payload bytes -> list of (name, value) str pairs. Raises FrameError
    on anything structurally broken or outside the subset, so bserve can
    turn it straight into a 400."""
    headers = []
    i = 0
    end = len(payload)
    while i < end:
        b = payload[i]
        i += 1
        if b & 0x80:
            # indexed field: one byte, the whole pair comes from the table
            idx = b & 0x7F
            if idx == 0 or idx > len(STATIC_TABLE):
                raise FrameError("bad static index %d" % idx)
            name, value = STATIC_TABLE[idx - 1]
            if value is None:
                raise FrameError("table entry %d has no value" % idx)
            headers.append((name, value))
            continue
        if b != 0x00:
            raise FrameError(
                "opcode %#02x not in our subset "
                "(huffman/dynamic table are not implemented)" % b)
        if i >= end:
            raise FrameError("truncated entry")
        nlen = payload[i]
        i += 1
        if i + nlen > end:
            raise FrameError("truncated header name")
        name = payload[i:i + nlen]
        i += nlen
        if i >= end:
            raise FrameError("truncated entry")
        vlen = payload[i]
        i += 1
        if i + vlen > end:
            raise FrameError("truncated header value")
        value = payload[i:i + vlen]
        i += vlen
        try:
            headers.append((name.decode("utf-8"), value.decode("utf-8")))
        except UnicodeDecodeError:
            raise FrameError("header is not utf-8")
    return headers


def hexdump(data, prefix=""):
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        hexcol = " ".join("%02x" % b for b in chunk)
        ascii_col = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("%s%08x  %-47s  %s" % (prefix, off, hexcol, ascii_col))
    return "\n".join(lines)
