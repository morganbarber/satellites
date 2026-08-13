"""
Unit tests for CRC16 calculation.
"""

import unittest
from ccsds.crc import calculate_ccsds_crc16


class TestCRC16(unittest.TestCase):
    """Tests CRC16 calculation against known standard vectors."""

    def test_crc16_standard_vector(self):
        """Tests standard 123456789 test vector."""
        test_data = b"123456789"
        crc = calculate_ccsds_crc16(test_data)
        self.assertEqual(crc, 0x29B1)

    def test_crc16_empty_bytes(self):
        """Tests CRC16 of empty byte array."""
        crc = calculate_ccsds_crc16(b"")
        self.assertEqual(crc, 0xFFFF)
