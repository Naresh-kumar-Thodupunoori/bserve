import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bframe


class FrameTest(unittest.TestCase):
    def test_round_trip(self):
        frame = bframe.pack_frame(bframe.TYPE_DATA, 0, 5, b"hello")
        self.assertEqual(len(frame), 14)  # 9-byte header + 5
        length, ftype, flags, sid = bframe.parse_frame_header(frame[:9])
        self.assertEqual((length, ftype, flags, sid), (5, 0, 0, 5))
        self.assertEqual(frame[9:], b"hello")

    def test_nine_byte_header_layout(self):
        frame = bframe.pack_frame(bframe.TYPE_HEADERS, 0x05, 1, b"")
        self.assertEqual(frame, bytes([0, 0, 0, 0x01, 0x05, 0, 0, 0, 1]))

    def test_reserved_stream_bit_masked_off(self):
        # same frame but with the reserved top bit of the sid set; must
        # read back as 5
        frame = bytes([0, 0, 5, 0, 0, 0x80, 0, 0, 5]) + b"hello"
        length, ftype, flags, sid = bframe.parse_frame_header(frame[:9])
        self.assertEqual((length, sid), (5, 5))

    def test_stream_id_over_31_bits(self):
        with self.assertRaises(ValueError):
            bframe.pack_frame(bframe.TYPE_DATA, 0, 0x80000000, b"")

    def test_payload_too_big_to_pack(self):
        with self.assertRaises(ValueError):
            bframe.pack_frame(bframe.TYPE_DATA, 0, 1, b"x" * 16385)

    def test_declared_length_over_limit(self):
        # claims 16385 bytes of payload
        with self.assertRaises(bframe.FrameError):
            bframe.parse_frame_header(b"\x00\x40\x01" + bytes([0, 0, 0, 0, 0, 1]))

    def test_spec_example_header(self):
        payload = bframe.encode_headers([
            (":method", "GET"),
            (":path", "/index.html"),
            (":authority", "localhost"),
            ("user-agent", "bcurl/0.1"),
        ])
        frame = bframe.pack_frame(
            bframe.TYPE_HEADERS, bframe.FLAG_END_STREAM | bframe.FLAG_END_HEADERS,
            1, payload)
        self.assertEqual(frame[:9], bytes([0, 0, 0x2e, 1, 5, 0, 0, 0, 1]))
        self.assertEqual(len(frame), 55)


class HeaderBlockTest(unittest.TestCase):
    def test_round_trip(self):
        pairs = [
            (":method", "GET"),
            (":path", "/x.txt"),
            (":authority", "localhost"),
            ("x-custom", "stuff"),
            ("content-type", "text/plain"),
        ]
        self.assertEqual(bframe.decode_headers(bframe.encode_headers(pairs)), pairs)

    def test_indexed_pairs_from_hpack_table(self):
        self.assertEqual(bframe.encode_headers([(":method", "GET")]), b"\x82")
        self.assertEqual(bframe.encode_headers([(":path", "/")]), b"\x84")
        self.assertEqual(bframe.encode_headers([(":path", "/index.html")]), b"\x85")
        self.assertEqual(bframe.encode_headers([(":status", "200")]), b"\x88")
        self.assertEqual(bframe.encode_headers([(":status", "404")]), b"\x8d")

    def test_literal_entry_layout(self):
        self.assertEqual(
            bframe.encode_headers([("x-a", "b")]),
            b"\x00\x03x-a\x01b",
        )

    def test_index_zero_is_invalid(self):
        with self.assertRaises(bframe.FrameError):
            bframe.decode_headers(b"\x80\x00")

    def test_index_past_table_end(self):
        with self.assertRaises(bframe.FrameError):
            bframe.decode_headers(b"\xbe\x00")  # index 62

    def test_valueless_entry_cannot_be_indexed(self):
        with self.assertRaises(bframe.FrameError):
            bframe.decode_headers(b"\x9f")  # 31 = content-type, no value

    def test_dynamic_table_opcodes_rejected(self):
        with self.assertRaises(bframe.FrameError):
            bframe.decode_headers(b"\x40\x00")  # literal, incremental indexing
        with self.assertRaises(bframe.FrameError):
            bframe.decode_headers(b"\x20")  # dynamic table size update

    def test_truncated_name(self):
        with self.assertRaises(bframe.FrameError):
            bframe.decode_headers(b"\x00\x05ab")

    def test_truncated_value(self):
        with self.assertRaises(bframe.FrameError):
            bframe.decode_headers(b"\x00\x01a\x04")

    def test_non_utf8_header(self):
        with self.assertRaises(bframe.FrameError):
            bframe.decode_headers(b"\x00\x02\xff\xfe\x01\x61")

    def test_empty_block(self):
        self.assertEqual(bframe.decode_headers(b""), [])


class HexdumpTest(unittest.TestCase):
    def test_two_lines_for_20_bytes(self):
        out = bframe.hexdump(bytes(range(20)))
        lines = out.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("00000000"))
        self.assertTrue(lines[1].startswith("00000010"))


if __name__ == "__main__":
    unittest.main()
