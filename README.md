# bserve

Course project for "HTTP, in binary". The idea is to take the parts of
HTTP that matter and speak them as binary frames over TCP instead of
text lines. It ended up being a small subset of HTTP/2, so what goes
over the wire looks like real HTTP/2 traffic.

bserve is the server side. It serves files out of a folder:

    ./bserve ./www 9000

and there is a small test client:

    python3 tests/bcurl.py -v localhost 9000 /index.html

The -v flag hexdumps every frame in both directions, which is the best
way to actually see the protocol working.

## what's in here

SPEC.md is the wire protocol, written so someone who has never seen my
code can build a client from it. That matters because it is basically
how this gets graded: another student builds the client side using only
the spec, and the instructor connects the two.

session.md is one complete request and response, hexdumped and
annotated byte by byte.

The code side: bframe.py is the codec for frames and header blocks,
bserve is the server, tests/test_frame.py is the unit tests, and
tests/bcurl.py is that mini client.

## tests

    python3 tests/test_frame.py
    python3 tests/test_server.py

test_frame is the codec: 19 unit tests, round trips plus every malformed
input I could think of. test_server starts the real server and drives it
over sockets: keep-alive, stream id rules, unknown frame skipping,
traversal, mid-frame disconnects.

## problems I ran into

- Answering 405 too fast desynced the connection. The server replied to
  a POST before reading its body, so the body frames got parsed as the
  next request. Garbage, then 400, then close. The fix is to always
  drain a request before answering it. This bug is also why malformed
  means close: binary framing has no way to resync, there is no CRLF to
  scan forward to.
- Unknown frames before the first request got rejected at first. But
  skipping unknown frame types has to work before the first HEADERS
  frame too, since a client may open with SETTINGS or PING. Easy fix,
  easy to miss.
- My smoke tests raced the server on startup. The retry loop burned
  through 50 connection attempts in a few milliseconds while the server
  was still binding. It just needed a sleep between attempts.
- Version 1 had my own 8-byte frame header. Moving to the real HTTP/2
  layout actually made the request smaller, because HPACK already has
  one byte codes for GET and /index.html. Read the standard before
  inventing your own.

## what I would add next

- a selectors event loop instead of one thread per connection
- the HPACK dynamic table, if I ever want to live dangerously
- SETTINGS negotiation for the max frame size
