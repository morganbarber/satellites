"""
TM Transfer Frame Protocol (CCSDS 132.0-B-3).
"""

import struct
from dataclasses import dataclass

from ccsds.crc import calculate_ccsds_crc16
from ccsds.exceptions import CRCError, ValidationError


@dataclass
class TMTransferFrame:
    """
    Represents a CCSDS Telemetry (TM) Transfer Frame (CCSDS 132.0-B-3).
    """
    scid: int
    vcid: int
    payload: bytes
    tfvn: int = 0
    ocf_flag: int = 0
    master_frame_count: int = 0
    vc_frame_count: int = 0
    sec_header_flag: int = 0
    synch_flag: int = 0
    packet_order_flag: int = 0
    segment_length_id: int = 3
    first_header_pointer: int = 0
    has_fecf: bool = True

    def __post_init__(self):
        self.validate()

    def validate(self):
        """Validates header fields and frame parameters."""
        if not 0 <= self.tfvn <= 3:
            raise ValidationError(f"TFVN must be 0-3, got {self.tfvn}")
        if not 0 <= self.scid <= 0x03FF:
            raise ValidationError(f"SCID out of 10-bit range (0-1023): {self.scid}")
        if not 0 <= self.vcid <= 0x07:
            raise ValidationError(f"VCID out of 3-bit range (0-7): {self.vcid}")

    def pack(self) -> bytes:
        """Packs TM Transfer Frame object into raw binary bytes."""
        word1 = ((self.tfvn & 0x03) << 14) | \
                ((self.scid & 0x03FF) << 4) | \
                ((self.vcid & 0x07) << 1) | \
                (self.ocf_flag & 0x01)

        word4 = ((self.sec_header_flag & 0x01) << 15) | \
                ((self.synch_flag & 0x01) << 14) | \
                ((self.packet_order_flag & 0x01) << 13) | \
                ((self.segment_length_id & 0x03) << 11) | \
                (self.first_header_pointer & 0x07FF)

        header = struct.pack(">HBBH", word1, self.master_frame_count & 0xFF, self.vc_frame_count & 0xFF, word4)
        frame_without_crc = header + self.payload

        if self.has_fecf:
            fecf = calculate_ccsds_crc16(frame_without_crc)
            return frame_without_crc + struct.pack(">H", fecf)
        return frame_without_crc

    @classmethod
    def unpack(cls, data: bytes, has_fecf: bool = True) -> "TMTransferFrame":
        """Unpacks raw binary bytes into a TMTransferFrame instance."""
        min_len = 8 if has_fecf else 6
        if len(data) < min_len:
            raise ValidationError(f"Data length ({len(data)}) too short for TM Transfer Frame (min {min_len} bytes)")

        word1, master_cnt, vc_cnt, word4 = struct.unpack(">HBBH", data[:6])

        tfvn = (word1 >> 14) & 0x03
        scid = (word1 >> 4) & 0x03FF
        vcid = (word1 >> 1) & 0x07
        ocf_flag = word1 & 0x01

        sec_header_flag = (word4 >> 15) & 0x01
        synch_flag = (word4 >> 14) & 0x01
        packet_order_flag = (word4 >> 13) & 0x01
        segment_length_id = (word4 >> 11) & 0x03
        first_header_pointer = word4 & 0x07FF

        payload_end = len(data) - (2 if has_fecf else 0)
        payload = data[6:payload_end]

        if has_fecf:
            received_crc = struct.unpack(">H", data[-2:])[0]
            computed_crc = calculate_ccsds_crc16(data[:payload_end])
            if received_crc != computed_crc:
                raise CRCError(f"TM FECF CRC mismatch! Received: 0x{received_crc:04X}, Computed: 0x{computed_crc:04X}")

        return cls(
            scid=scid,
            vcid=vcid,
            payload=payload,
            tfvn=tfvn,
            ocf_flag=ocf_flag,
            master_frame_count=master_cnt,
            vc_frame_count=vc_cnt,
            sec_header_flag=sec_header_flag,
            synch_flag=synch_flag,
            packet_order_flag=packet_order_flag,
            segment_length_id=segment_length_id,
            first_header_pointer=first_header_pointer,
            has_fecf=has_fecf
        )
