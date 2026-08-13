"""
Unit tests for payload format extraction and formatting.
"""

import unittest

from ccsds.models.space_packet import SpacePacket
from ccsds.transport.payload import (
    extract_counter_from_payload,
    format_counter_payload,
    parse_response_payload,
)


class TestPayloadUtilities(unittest.TestCase):
    def test_extract_counter_and_formatting(self):
        cnt1, data1, fmt1 = extract_counter_from_payload(b"0x01:ACK", 0)
        self.assertEqual(cnt1, 1)
        self.assertEqual(fmt1, "hex_text")
        self.assertEqual(format_counter_payload(1, b"GETFLAG", fmt1), b"0x01:GETFLAG")

        cnt2, data2, fmt2 = extract_counter_from_payload(b"2:ACK", 1)
        self.assertEqual(cnt2, 2)
        self.assertEqual(fmt2, "dec_text")
        self.assertEqual(format_counter_payload(2, b"GETFLAG", fmt2), b"2:GETFLAG")

        cnt3, data3, fmt3 = extract_counter_from_payload(b"\x03:ACK", 2)
        self.assertEqual(cnt3, 3)
        self.assertEqual(fmt3, "binary_colon")
        self.assertEqual(format_counter_payload(3, b"GETFLAG", fmt3), b"\x03:GETFLAG")

    def test_parse_response_payload_valid_packet(self):
        sp_in = SpacePacket(apid=42, payload=b"HELLO_WORLD")
        packed = sp_in.pack()
        sp_out = parse_response_payload(packed)
        self.assertEqual(sp_out.apid, 42)
        self.assertEqual(sp_out.payload, b"HELLO_WORLD")

    def test_parse_response_payload_fallback(self):
        sp = parse_response_payload(b"XYZ")
        self.assertEqual(sp.apid, 0)
        self.assertEqual(sp.payload, b"XYZ")
