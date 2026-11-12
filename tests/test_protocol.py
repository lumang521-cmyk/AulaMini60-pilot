# -*- coding: utf-8 -*-
import unittest

from mini60pilot import protocol


class ProtocolTests(unittest.TestCase):
    def test_read_packet_layout(self):
        packet = protocol.build_read(2, 24, 24)
        self.assertEqual(len(packet), 64)
        self.assertEqual(packet[0], 0xAA)
        self.assertEqual(packet[1], 0x12)
        self.assertEqual(packet[2], 24)
        self.assertEqual(packet[3], 24)
        self.assertEqual(packet[4], 0)
        self.assertEqual(packet[6], 0)

    def test_read_default_chunk(self):
        packet = protocol.build_read(2, 0)
        self.assertEqual(packet[2], protocol.CHUNK_SIZE)
        self.assertEqual(protocol.CHUNK_SIZE, 56)

    def test_write_packet_last_flag(self):
        packet = protocol.build_write(2, 56, b"\x01\x02\x03", last=True)
        self.assertEqual(packet[1], 0x22)
        self.assertEqual(packet[2], 3)
        self.assertEqual(packet[3], 56)
        self.assertEqual(packet[6], 1)
        self.assertEqual(packet[8:11], b"\x01\x02\x03")

    def test_write_rejects_oversize(self):
        with self.assertRaises(ValueError):
            protocol.build_write(2, 0, bytes(57), last=False)

    def test_parse_response(self):
        raw = bytearray(64)
        raw[0] = 0x55
        raw[1] = 0x12
        raw[2] = 0x18
        raw[3] = 24
        raw[8:11] = b"\xAA\xBB\xCC"
        response = protocol.parse_response(bytes(raw))
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.command, 0x12)
        self.assertEqual(response.address, 24)
        self.assertEqual(response.payload, bytes(raw[8:32]))

    def test_parse_response_with_report_id(self):
        raw = bytearray(65)
        raw[0] = 0x00
        raw[1] = 0x55
        raw[2] = 0x13
        raw[3] = 0x18
        response = protocol.parse_response(bytes(raw))
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.command, 0x13)

    def test_parse_garbage(self):
        self.assertIsNone(protocol.parse_response(b"hello"))

    def test_chunk_ranges(self):
        ranges = protocol.chunk_ranges(512)
        self.assertEqual(len(ranges), 10)
        self.assertEqual(ranges[0], (0, 56))
        self.assertEqual(ranges[-1], (504, 8))
        self.assertEqual(sum(size for _, size in ranges), 512)

    def test_page_commands(self):
        self.assertEqual(protocol.read_command(1), 0x11)
        self.assertEqual(protocol.write_command(3), 0x23)


if __name__ == "__main__":
    unittest.main()
