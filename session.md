# one request, one response — annotated

Real capture of `tests/bcurl.py -v localhost 9000 /index.html` talking to
`./bserve ./www 9000`. The frame layer and header compression are
HTTP/2's (RFC 9113, RFC 7541), so the bytes below look like genuine
HTTP/2 — because they are, minus the parts BHTTP/2 cuts.

## the request — one HEADERS frame, 55 bytes

    00000000  00 00 2e 01 05 00 00 00 01 82 85 00 0a 3a 61 75  .............:au
    00000010  74 68 6f 72 69 74 79 09 6c 6f 63 61 6c 68 6f 73  thority.localhos
    00000020  74 00 0a 75 73 65 72 2d 61 67 65 6e 74 09 62 63  t..user-agent.bc
    00000030  75 72 6c 2f 30 2e 32                             url/0.2

Byte by byte:

- `00 00 2e` — Length = 46, a 24-bit field, payload only. 9 + 46 = the
  55 bytes you see.
- `01` — Type 1 = HEADERS (HTTP/2's number).
- `05` — Flags: END_STREAM (0x1) | END_HEADERS (0x4). A GET has no
  body, so one frame carries the whole request *and* closes the stream.
- `00 00 00 01` — StreamId = 1: odd, first stream, as HTTP/2 requires
  from a client. The top bit of this field is reserved — sent 0, and
  masked off on receipt no matter what arrives.
- `82` — the entire `:method: GET`. HPACK static table entry 2, one
  byte.
- `85` — the entire `:path: /index.html`. Table entry 5 — someone at
  Google got `/index.html` its own table slot because it was the most
  requested path on the web. Method + path = two bytes total.
- `00 0a :authority 09 localhost` — a literal: opcode 0x00, name
  length 10, name, value length 9, value. No entry for `localhost`, so
  it travels as text.
- `00 0a user-agent 09 bcurl/0.2` — same, literal.

## the response, frame 1 — HEADERS, 109 bytes

    00000000  00 00 64 01 04 00 00 00 01 88 00 0c 63 6f 6e 74  ..d.........cont
    00000010  65 6e 74 2d 74 79 70 65 09 74 65 78 74 2f 68 74  ent-type.text/ht
    00000020  6d 6c 00 0e 63 6f 6e 74 65 6e 74 2d 6c 65 6e 67  ml..content-leng
    00000030  74 68 03 31 36 35 00 06 73 65 72 76 65 72 0a 62  th.165..server.b
    00000040  73 65 72 76 65 2f 30 2e 32 00 04 64 61 74 65 1d  serve/0.2..date.
    00000050  57 65 64 2c 20 30 37 20 4f 63 74 20 32 30 32 36  Wed, 07 Oct 2026
    00000060  20 30 35 3a 31 38 3a 32 30 20 47 4d 54            05:18:20 GMT

- `00 00 64` — Length = 100. `01` — HEADERS. `00 00 00 01` — stream 1,
  echoed from the request.
- `04` — Flags: END_HEADERS only. **No END_STREAM** — a DATA frame
  follows. Compare with the request, which set both.
- `88` — the entire `:status: 200`. Table entry 8, one byte. (404 would
  be `8d`, 400 `8c`.)
- `00 0c content-type 09 text/html`, `00 0e content-length 03 165`,
  literals — the static table carries these *names* but not with these
  values, and BHTTP/2 only uses the table for exact name+value pairs.

## the response, frame 2 — DATA, 174 bytes

    00000000  00 00 a5 00 01 00 00 00 01 3c 21 64 6f 63 74 79  .........<!docty
    00000010  70 65 20 68 74 6d 6c 3e 0a 3c 68 74 6d 6c 3e 0a  pe html>.<html>.
    00000020  3c 68 65 61 64 3e 3c 74 69 74 6c 65 3e 69 74 20  <head><title>it
    00000030  77 6f 72 6b 73 3c 2f 74 69 74 6c 65 3e 3c 2f 68  works</title></h
    00000040  65 61 64 3e 0a 3c 62 6f 64 79 3e 0a 3c 68 31 3e  ead>.<body>.<h1>
    00000050  69 74 20 77 6f 72 6b 73 3c 2f 68 31 3e 0a 3c 70  it works</h1>.<p
    00000060  3e 74 68 69 73 20 70 61 67 65 20 63 61 6d 65 20  >this page came
    00000070  6f 75 74 20 6f 66 20 61 20 44 41 54 41 20 66 72  out of a DATA fr
    00000080  61 6d 65 2c 20 6e 6f 74 20 61 20 74 65 78 74 20  ame, not a text
    00000090  70 72 6f 74 6f 63 6f 6c 2e 3c 2f 70 3e 0a 3c 2f  protocol.</p>.</
    000000a0  62 6f 64 79 3e 0a 3c 2f 68 74 6d 6c 3e 0a        body>.</html>.

- `00 00 a5` — Length = 165, exactly the content-length promised in the
  HEADERS frame.
- `00` — Type 0 = DATA. `01` — END_STREAM: last frame of the response.
- Payload is the raw file. Nothing escapes it, nothing wraps it.

## what this transcript shows

- Two bytes (`82 85`) carry what text HTTP needs a whole request line
  for. That is the entire point of header compression.
- No CRLF anywhere, no request line, no `HTTP/1.1`. The only readable
  text is literal header values; the ASCII column is a convenience, not
  the protocol.
- Every multi-byte number reads big-endian, so the same bytes parse on
  any machine.
- The connection is still open after this exchange — 200 keeps it
  alive, and the next request must arrive on a *higher odd* stream id.
