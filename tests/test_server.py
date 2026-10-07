"""Socket-level tests: spin up the real bserve and talk BHTTP/2 to it.

Usage: python3 tests/test_server.py
"""
import os
import socket
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bframe

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = "127.0.0.1"

server = None
port = 0


def free_port():
    s = socket.socket()
    s.bind((HOST, 0))
    p = s.getsockname()[1]
    s.close()
    return p


def setUpModule():
    global server, port
    port = free_port()
    server = subprocess.Popen(
        [os.path.join(REPO, "bserve"), os.path.join(REPO, "www"),
         str(port)])
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            socket.create_connection((HOST, port), timeout=1).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("bserve never came up")


def tearDownModule():
    server.terminate()
    server.wait()


def connect():
    s = socket.create_connection((HOST, port), timeout=5)
    s.settimeout(5)
    return s


def read_response(sock):
    headers = None
    body = b""
    while True:
        frame = bframe.read_frame(sock)
        assert frame is not None, "close before the response finished"
        ftype, flags, sid, payload = frame
        if ftype == bframe.TYPE_HEADERS:
            headers = dict(bframe.decode_headers(payload))
        elif ftype == bframe.TYPE_DATA:
            body += payload
        if flags & bframe.FLAG_END_STREAM:
            return headers, body


def send_get(sock, path, sid=1):
    payload = bframe.encode_headers(
        [(":method", "GET"), (":path", path), (":authority", "localhost")])
    sock.sendall(bframe.pack_frame(
        bframe.TYPE_HEADERS,
        bframe.FLAG_END_STREAM | bframe.FLAG_END_HEADERS, sid, payload))


class ServingTest(unittest.TestCase):
    def test_file_200(self):
        s = connect()
        send_get(s, "/hello.txt")
        headers, body = read_response(s)
        s.close()
        self.assertEqual(headers[":status"], "200")
        self.assertEqual(body, b"hello from inside a DATA frame\n")

    def test_root_serves_index(self):
        s = connect()
        send_get(s, "/")
        headers, body = read_response(s)
        s.close()
        self.assertEqual(headers[":status"], "200")
        self.assertIn(b"<h1>", body)

    def test_missing_file_404_and_keep_alive(self):
        s = connect()
        send_get(s, "/nope.txt")
        headers, _ = read_response(s)
        self.assertEqual(headers[":status"], "404")
        send_get(s, "/hello.txt", sid=3)
        headers, _ = read_response(s)
        self.assertEqual(headers[":status"], "200")
        s.close()

    def test_traversal_404(self):
        s = connect()
        send_get(s, "/../SPEC.md")
        headers, _ = read_response(s)
        s.close()
        self.assertEqual(headers[":status"], "404")

    def test_binary_file_exact(self):
        s = connect()
        send_get(s, "/pic.bin")
        headers, body = read_response(s)
        s.close()
        self.assertEqual(headers[":status"], "200")
        self.assertEqual(body, bytes(range(256)))
        self.assertEqual(headers["content-type"],
                         "application/octet-stream")

    def test_post_405_and_body_does_not_desync(self):
        s = connect()
        payload = bframe.encode_headers(
            [(":method", "POST"), (":path", "/x")])
        s.sendall(bframe.pack_frame(
            bframe.TYPE_HEADERS, bframe.FLAG_END_HEADERS, 1, payload))
        s.sendall(bframe.pack_frame(
            bframe.TYPE_DATA, bframe.FLAG_END_STREAM, 1, b"upload"))
        headers, _ = read_response(s)
        self.assertEqual(headers[":status"], "405")
        send_get(s, "/hello.txt", sid=3)
        headers, _ = read_response(s)
        self.assertEqual(headers[":status"], "200")
        s.close()


class FrameRuleTest(unittest.TestCase):
    def test_unknown_frame_skipped(self):
        s = connect()
        s.sendall(bframe.pack_frame(9, 0, 1, b"junkjunk"))
        send_get(s, "/hello.txt")
        headers, _ = read_response(s)
        s.close()
        self.assertEqual(headers[":status"], "200")

    def test_unknown_frame_flags_are_inert(self):
        # END_STREAM on an unknown type means nothing: skip the frame,
        # the connection must still answer the next real request
        s = connect()
        s.sendall(bframe.pack_frame(6, bframe.FLAG_END_STREAM, 0, b""))
        send_get(s, "/hello.txt")
        headers, _ = read_response(s)
        s.close()
        self.assertEqual(headers[":status"], "200")

    def test_even_stream_id_400_close(self):
        s = connect()
        send_get(s, "/hello.txt", sid=44)
        headers, _ = read_response(s)
        self.assertEqual(headers[":status"], "400")
        self.assertEqual(s.recv(1024), b"")  # closed
        s.close()

    def test_reused_stream_id_400(self):
        s = connect()
        send_get(s, "/hello.txt", sid=1)
        read_response(s)
        send_get(s, "/hello.txt", sid=1)
        headers, _ = read_response(s)
        s.close()
        self.assertEqual(headers[":status"], "400")

    def test_data_on_wrong_stream_400(self):
        s = connect()
        payload = bframe.encode_headers(
            [(":method", "GET"), (":path", "/hello.txt")])
        s.sendall(bframe.pack_frame(
            bframe.TYPE_HEADERS, bframe.FLAG_END_HEADERS, 3, payload))
        s.sendall(bframe.pack_frame(
            bframe.TYPE_DATA, bframe.FLAG_END_STREAM, 5, b""))
        headers, _ = read_response(s)
        self.assertEqual(headers[":status"], "400")
        s.close()

    def test_padded_data_400(self):
        s = connect()
        payload = bframe.encode_headers(
            [(":method", "GET"), (":path", "/hello.txt")])
        s.sendall(bframe.pack_frame(
            bframe.TYPE_HEADERS, bframe.FLAG_END_HEADERS, 1, payload))
        s.sendall(bframe.pack_frame(
            bframe.TYPE_DATA, bframe.FLAG_END_STREAM | 0x08, 1, b"xx"))
        headers, _ = read_response(s)
        s.close()
        self.assertEqual(headers[":status"], "400")

    def test_absurd_declared_length_400_close(self):
        s = connect()
        s.sendall(b"\xff\xff\xff" + bytes([0, 0, 0, 0, 0, 1]))
        headers, _ = read_response(s)
        self.assertEqual(headers[":status"], "400")
        self.assertEqual(s.recv(1024), b"")
        s.close()

    def test_mid_frame_death_is_silent(self):
        # half a frame then goodbye: spec says close silently, no 400
        s = connect()
        s.sendall(b"\x00\x00\x00\x05\x00")  # 5 of 9 header bytes
        s.shutdown(socket.SHUT_WR)
        self.assertEqual(s.recv(1024), b"")  # closed with nothing sent
        s.close()
        # and the server survives
        s = connect()
        send_get(s, "/hello.txt")
        headers, _ = read_response(s)
        s.close()
        self.assertEqual(headers[":status"], "200")


class HeaderRuleTest(unittest.TestCase):
    def request(self, pairs):
        s = connect()
        s.sendall(bframe.pack_frame(
            bframe.TYPE_HEADERS,
            bframe.FLAG_END_STREAM | bframe.FLAG_END_HEADERS, 1,
            bframe.encode_headers(pairs)))
        headers, _ = read_response(s)
        s.close()
        return headers

    def test_unknown_pseudo_header_400(self):
        h = self.request([(":method", "GET"), (":path", "/x"),
                          (":scheme", "http")])
        self.assertEqual(h[":status"], "400")

    def test_pseudo_after_regular_400(self):
        h = self.request([("x-a", "1"), (":method", "GET"),
                          (":path", "/x")])
        self.assertEqual(h[":status"], "400")

    def test_missing_path_400(self):
        h = self.request([(":method", "GET")])
        self.assertEqual(h[":status"], "400")


if __name__ == "__main__":
    unittest.main()
