#!/usr/bin/env python3
"""mini bcurl — test harness for bserve, written against SPEC.md only.

usage: bcurl.py [-v] <host> <port> <path>

body goes to stdout, -v hexdumps the frames to stderr, exit code is
1 on any 4xx/5xx. One request, one connection, then done.
"""
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bframe


def main():
    verbose = "-v" in sys.argv
    args = [a for a in sys.argv[1:] if a != "-v"]
    if len(args) != 3:
        sys.exit("usage: bcurl.py [-v] <host> <port> <path>")
    host, port, path = args[0], int(args[1]), args[2]

    sock = socket.create_connection((host, port), timeout=5)

    payload = bframe.encode_headers([
        (":method", "GET"),
        (":path", path),
        (":authority", host),
        ("user-agent", "bcurl/0.2"),
    ])
    frame = bframe.pack_frame(
        bframe.TYPE_HEADERS,
        bframe.FLAG_END_STREAM | bframe.FLAG_END_HEADERS,
        1, payload)
    if verbose:
        print("--> HEADERS, %d bytes" % len(frame), file=sys.stderr)
        print(bframe.hexdump(frame, "  "), file=sys.stderr)
    sock.sendall(frame)

    status = None
    body = b""
    while True:
        f = bframe.read_frame(sock)
        if f is None:
            sys.exit("server closed without finishing the response")
        ftype, flags, sid, fpayload = f
        if verbose:
            raw = bframe.pack_frame(ftype, flags, sid, fpayload)
            print("<-- %s, %d bytes" %
                  ("HEADERS" if ftype == bframe.TYPE_HEADERS else "DATA",
                   len(raw)), file=sys.stderr)
            print(bframe.hexdump(raw, "  "), file=sys.stderr)
        if ftype == bframe.TYPE_HEADERS:
            headers = dict(bframe.decode_headers(fpayload))
            status = int(headers.get(":status", "0"))
            if verbose:
                for k, v in headers.items():
                    print("  %s: %s" % (k, v), file=sys.stderr)
        elif ftype == bframe.TYPE_DATA:
            body += fpayload
        if flags & bframe.FLAG_END_STREAM:
            break
    sock.close()

    sys.stdout.buffer.write(body)
    if status >= 400:
        sys.exit(1)


if __name__ == "__main__":
    main()
