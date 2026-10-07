# BHTTP/2 — "HTTP, in binary"

A teaching subset of HTTP/2's framing over TCP: binary frames, HPACK-style
header compression, one request per stream. This document is the complete
wire contract — two implementations that have never seen each other's code
should interoperate using only this file. Where we follow HTTP/2 (RFC 9113)
and HPACK (RFC 7541) exactly, we say so; where we cut something, the spec
says what happens instead.

Everything on the wire is big-endian. No field depends on the sender's
host architecture.

## 1. Roles and connection model

One TCP connection, client to server. The client sends requests as frames
on streams; the server answers each with exactly one response, in request
order. There is no connection preface — the first frame may be a request.
The server closes the connection after any malformed request (section 6);
everything else keeps the connection open.

## 2. Frame layout

Every frame is a 9-byte header followed by `Length` payload bytes — the
same layout as RFC 9113 section 4.1:

     0                   1                   2                   3
     0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
    +---------------------------------------------------------------+
    |                         Length (24)                           |
    +---------------------------------------+-----------------------+
    |               Type (8)                |       Flags (8)       |
    +-------+-------------------------------------------------------+
    | R (1) |                     StreamId (31)                     |
    +-------+-------------------------------------------------------+

| Field     | Width | Meaning |
|-----------|-------|---------|
| Length    | 24    | payload length, not counting the 9 header bytes |
| Type      | 8     | see 2.1 |
| Flags     | 8     | see 2.2 |
| R         | 1     | reserved bit before StreamId; send 0, ignore on receipt |
| StreamId  | 31    | identifies the request; see 2.3 |

### 2.1 Frame types

We implement two; the numbers are HTTP/2's.

| Type | Name    | Payload                 |
|------|---------|-------------------------|
| 0x0  | DATA    | raw body bytes          |
| 0x1  | HEADERS | header block, section 4 |

Every other type value — including HTTP/2's SETTINGS (0x4), PING (0x6),
GOAWAY (0x7), WINDOW_UPDATE (0x8), CONTINUATION (0x9) — is not part of
BHTTP/2. Receivers MUST skip unknown types: read `Length` payload bytes,
discard them, keep going. Unknown types are never an error. (This is
HTTP/2's own rule for unknown types, RFC 9113 section 4.1.)

### 2.2 Flags

| Flag | Value | DATA | HEADERS |
|------|-------|------|---------|
| END_STREAM  | 0x01 | last frame of the body | no body follows |
| END_HEADERS | 0x04 | —    | this frame ends the header block |

Rules, following HTTP/2:

- A HEADERS frame MUST set END_HEADERS. (Full HTTP/2 allows it to be
  clear, followed by CONTINUATION frames; BHTTP/2 has no CONTINUATION,
  so END_HEADERS is always set. A HEADERS without it is malformed.)
- PADDED (0x08) MUST NOT be set on a HEADERS or a DATA frame, and
  PRIORITY (0x20) not on a HEADERS frame. We do not parse padded or
  prioritized frames; the bit set is malformed.
- Flags belong to the frame type that defines them. An unknown frame
  type is skipped whole, flags and all — its flag bits never carry
  meaning over to the request.
- Any other flag bit is reserved: ignore it, never reject for it.

### 2.3 Limits and streams

- Maximum payload is 16384 bytes — HTTP/2's default
  SETTINGS_MAX_FRAME_SIZE (RFC 9113 section 6.9.2). We have no SETTINGS
  negotiation, so it is fixed. A frame *declaring* Length > 16384 is
  malformed: 400, close. Bigger bodies travel as several frames.
- The client uses odd stream ids, starting at 1, each strictly greater
  than the last (HTTP/2 section 5.1.1). Stream 0 and even ids from a
  client are malformed: 400, close. Every DATA frame of a request
  carries that request's stream id too — any other id is malformed.
  The server sends the response on the request's stream id, unchanged.

## 3. Messages

### 3.1 Request

One HEADERS frame on a fresh odd stream, carrying at least `:method` and
`:path`. GET has no body, so the frame carries END_STREAM too. A request
*with* a body is HEADERS (no END_STREAM) followed by DATA frames, the
last with END_STREAM — the server reads and discards it. The request
ends at END_STREAM on a HEADERS or DATA frame; END_STREAM on an unknown
frame type means nothing. A connection that closes cleanly before
END_STREAM is a malformed request: the server answers 400 on that
stream, then closes.

### 3.2 Response

HEADERS carrying at least `:status`, then zero or more DATA frames (the
body), the last with END_STREAM. An empty body is a single HEADERS frame
with both END_HEADERS and END_STREAM. All response frames carry the
request's stream id.

## 4. Header block (HPACK subset)

A HEADERS payload is a sequence of entries, decoded with a static table
only. We take two of HPACK's opcodes (RFC 7541 section 6) and drop the
stateful ones:

| First byte | Opcode | Meaning |
|------------|--------|---------|
| `1000 0000` + idx | indexed header field | the whole name+value pair is table entry `idx` |
| `0000 0000` | literal, no indexing | name and value follow as length-prefixed strings |
| anything else | — | malformed: 400, close |

Details:

- **Indexed**: one byte, `0x80 | idx`, where 1 <= idx <= 61 selects from
  the static table below. The entry must have a value (entries marked —
  cannot be indexed; that is HPACK's own rule). idx 0 or idx > 61 is
  malformed.
- **Literal**: `0x00`, a 1-byte name length, the name, a 1-byte value
  length, the value. We never set HPACK's Huffman bit: strings are raw
  UTF-8, max 255 bytes each.
- Dropped on purpose: literal *with* incremental indexing (`0x40`–`0x7F`)
  and dynamic table size updates (`0x20`–`0x3F`) mutate decoder state we
  do not keep, and Huffman needs a 256-entry code table. Seeing either
  opcode range is malformed.

Pseudo-headers (`:method`, `:path`, `:authority`) follow HTTP/2's rules:
they must appear before every regular header, must not repeat, and any
other pseudo-header in a request is malformed. Responses use `:status`.
Unknown *regular* header names are ignored silently, never an error.

Static table (RFC 7541 appendix A, all 61 entries; — means no value):

| idx | name | value | | idx | name | value |
|-----|------|-------|-|-----|------|-------|
| 1 | :authority | — | | 32 | cookie | — |
| 2 | :method | GET | | 33 | date | — |
| 3 | :method | POST | | 34 | etag | — |
| 4 | :path | / | | 35 | expect | — |
| 5 | :path | /index.html | | 36 | expires | — |
| 6 | :scheme | http | | 37 | from | — |
| 7 | :scheme | https | | 38 | host | — |
| 8 | :status | 200 | | 39 | if-match | — |
| 9 | :status | 204 | | 40 | if-modified-since | — |
| 10 | :status | 206 | | 41 | if-none-match | — |
| 11 | :status | 304 | | 42 | if-range | — |
| 12 | :status | 400 | | 43 | if-unmodified-since | — |
| 13 | :status | 404 | | 44 | last-modified | — |
| 14 | :status | 500 | | 45 | link | — |
| 15 | accept-charset | — | | 46 | location | — |
| 16 | accept-encoding | gzip, deflate | | 47 | max-forwards | — |
| 17 | accept-language | — | | 48 | proxy-authenticate | — |
| 18 | accept-ranges | — | | 49 | proxy-authorization | — |
| 19 | accept | — | | 50 | range | — |
| 20 | access-control-allow-origin | — | | 51 | referer | — |
| 21 | age | — | | 52 | refresh | — |
| 22 | allow | — | | 53 | retry-after | — |
| 23 | authorization | — | | 54 | server | — |
| 24 | cache-control | — | | 55 | set-cookie | — |
| 25 | content-disposition | — | | 56 | strict-transport-security | — |
| 26 | content-encoding | — | | 57 | transfer-encoding | — |
| 27 | content-language | — | | 58 | user-agent | — |
| 28 | content-length | — | | 59 | vary | — |
| 29 | content-location | — | | 60 | via | — |
| 30 | content-range | — | | 61 | www-authenticate | — |
| 31 | content-type | — | | | | |

## 5. Example

`GET /index.html` — one frame, 55 bytes total:

    00 00 2e 01 05 00 00 00 01    Length 46, HEADERS, END_STREAM|END_HEADERS, stream 1
    82                            :method "GET"       (table entry 2 — one byte)
    85                            :path "/index.html" (table entry 5 — one byte)
    00 0a :authority 09 localhost literal
    00 0a user-agent 09 bcurl/0.2 literal

The 200 reply for an empty file is a single HEADERS frame; `:status`,
`content-type` and `content-length` header entries, both END flags set.
A file with a body adds DATA frames, the last with END_STREAM.

## 6. Status codes and errors

| Code | When | Connection after |
|------|------|------------------|
| 200  | file found and sent | stays open |
| 400  | anything malformed: bad frame layout, declared length over 16384, DATA first, HEADERS without END_HEADERS, PADDED/PRIORITY set, stream id 0/even/reused or DATA on the wrong stream, broken header block, bad opcode, missing/late/duplicate pseudo-headers, missing :method/:path | closed |
| 404  | no such file under the root | stays open |
| 405  | method is not GET | stays open |

If the connection dies mid-frame (short header, payload shorter than
declared), the server just closes. There is nobody left to tell.

## 7. Path rules

- `:path` maps to a file under the document root:
  `./bserve ./www 9000` + `:path /index.html` reads `./www/index.html`.
- `/` means `/index.html` (hence its table entry).
- No percent-decoding. The path is used literally.
- A path that tries to escape the root (`..` as a path segment, or
  anything that resolves outside the root — symlinks count) gets 404 —
  same answer as a missing file, on purpose.

## 8. Things an implementer can assume

- One request at a time per connection; responses in request order.
- No preface, no SETTINGS, no flow control: DATA flows as fast as TCP
  allows, bounded by the 16384 frame cap.
- Header blocks always fit one HEADERS frame (there is no CONTINUATION).
- Reserved bits and unknown flags are ignored, never rejected.
