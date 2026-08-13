"""
Unit tests for CCSDS Protocol Data Models (SpacePacket, TCTransferFrame, TMTransferFrame).
"""

import unittest

from ccsds.exceptions import CRCError, ValidationError
from ccsds.models import SpacePacket, TCTransferFrame, TMTransferFrame


class TestProtocolModels(unittest.TestCase):
    """Tests packing, unpacking, and validation for CCSDS protocol models."""

    def test_space_packet_pack_unpack(self):
        """Tests SpacePacket binary serialization roundtrip."""
        payload = b"\x01\x02\x03\x04\x05"
        sp = SpacePacket(apid=42, payload=payload, seq_flags=1, seq_count=100)
        packed = sp.pack()

        unpacked = SpacePacket.unpack(packed)
        self.assertEqual(unpacked.apid, 42)
        self.assertEqual(unpacked.seq_flags, 1)
        self.assertEqual(unpacked.seq_count, 100)
        self.assertEqual(unpacked.payload, payload)

    def test_tc_transfer_frame_pack_unpack(self):
        """Tests TCTransferFrame binary serialization roundtrip."""
        payload = b"TEST_PAYLOAD"
        tc = TCTransferFrame(scid=123, vcid=4, payload=payload, bypass=0, seq_num=77)
        packed = tc.pack()

        unpacked = TCTransferFrame.unpack(packed)
        self.assertEqual(unpacked.scid, 123)
        self.assertEqual(unpacked.vcid, 4)
        self.assertEqual(unpacked.bypass, 0)
        self.assertEqual(unpacked.seq_num, 77)
        self.assertEqual(unpacked.payload, payload)

    def test_tm_transfer_frame_pack_unpack(self):
        """Tests TMTransferFrame binary serialization roundtrip."""
        payload = b"TELEMETRY_DATA"
        tm = TMTransferFrame(scid=12, vcid=4, payload=payload, master_frame_count=10, vc_frame_count=2)
        packed = tm.pack()
        unpacked = TMTransferFrame.unpack(packed)
        self.assertEqual(unpacked.scid, 12)
        self.assertEqual(unpacked.vcid, 4)
        self.assertEqual(unpacked.master_frame_count, 10)
        self.assertEqual(unpacked.vc_frame_count, 2)
        self.assertEqual(unpacked.payload, payload)

    def test_tc_crc_failure(self):
        """Tests CRC validation failure handling for corrupted frames."""
        payload = b"TEST"
        tc = TCTransferFrame(scid=1, vcid=1, payload=payload)
        packed = bytearray(tc.pack())
        packed[-1] ^= 0xFF  # Corrupt CRC

        with self.assertRaises((ValueError, CRCError)):
            TCTransferFrame.unpack(bytes(packed))

    def test_out_of_range_validation(self):
        """Tests value range boundary validation."""
        with self.assertRaises((ValueError, ValidationError)):
            SpacePacket(apid=3000, payload=b"")
        with self.assertRaises((ValueError, ValidationError)):
            TCTransferFrame(scid=2000, vcid=1, payload=b"")
