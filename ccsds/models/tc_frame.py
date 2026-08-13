"""
TC Transfer Frame Protocol (CCSDS 232.0-B-3).
"""

import struct
from dataclasses import dataclass

from ccsds.crc import calculate_ccsds_crc16
from ccsds.exceptions import CRCError, ValidationError


@dataclass
class TCTransferFrame:
    """
    Represents a CCSDS Telecommand (TC) Transfer Frame (CCSDS 232.0-B-3).
    """
    scid: int
    vcid: int
    payload: bytes
    tfvn: int = 0
    bypass: int = 1            # 0 = Type A (Sequence-Controlled), 1 = Type B (Expedited)
    control: int = 0           # 0 = TC Data, 1 = Control Command (COP-1)
    reserved: int = 0
    seq_num: int = 0           # 8-bit frame sequence number N(S)
    has_fecf: bool = True       # Frame Error Control Field (CRC16)

    def __post_init__(self):
        self.validate()

    def validate(self):
        if not (0 <= self.tfvn <= 3):
            raise ValidationError(f"TFVN must be 0-3, got {self.tfvn}")
        if not (0 <= self.bypass <= 1):
            raise ValidationError(f"Bypass flag must be 0 or 1, got {self.bypass}")
        if not (0 <= self.control <= 1):
            raise ValidationError(f"Control flag must be 0 or 1, got {self.control}")
        if not (0 <= self.scid <= 0x03FF):
            raise ValidationError(f"SCID out of 10-bit range (0-1023): {self.scid}")
        if not (0 <= self.vcid <= 0x3F):
            raise ValidationError(f"VCID out of 6-bit range (0-63): {self.vcid}")
        if not (0 <= self.seq_num <= 0xFF):
            raise ValidationError(f"Sequence number out of 8-bit range (0-255): {self.seq_num}")

    def pack(self) -> bytes:
        header_len = 5
        crc_len = 2 if self.has_fecf else 0
        total_frame_length = header_len + len(self.payload) + crc_len

        if total_frame_length > 1024:
            raise ValidationError(f"Frame length {total_frame_length} exceeds CCSDS TC max 1024 bytes.")

        frame_length_field = (total_frame_length - 1) & 0x03FF

        word1 = ((self.tfvn & 0x03) << 14) | \
                ((self.bypass & 0x01) << 13) | \
                ((self.control & 0x01) << 12) | \
                ((self.reserved & 0x03) << 10) | \
                (self.scid & 0x03FF)

        word2 = ((self.vcid & 0x3F) << 10) | frame_length_field
        word3 = (self.seq_num & 0xFF) if self.bypass == 0 else 0

        header = struct.pack(">HHB", word1, word2, word3)
        frame_without_crc = header + self.payload

        if self.has_fecf:
            fecf = calculate_ccsds_crc16(frame_without_crc)
            return frame_without_crc + struct.pack(">H", fecf)
        return frame_without_crc

    @classmethod
    def unpack(cls, data: bytes, has_fecf: bool = True) -> "TCTransferFrame":
        min_len = 7 if has_fecf else 5
        if len(data) < min_len:
            raise ValidationError(f"Data length ({len(data)}) too short for TC Transfer Frame (min {min_len} bytes)")

        word1, word2, word3 = struct.unpack(">HHB", data[:5])

        tfvn = (word1 >> 14) & 0x03
        bypass = (word1 >> 13) & 0x01
        control = (word1 >> 12) & 0x01
        reserved = (word1 >> 10) & 0x03
        scid = word1 & 0x03FF

        vcid = (word2 >> 10) & 0x3F
        frame_length_field = word2 & 0x03FF
        total_len = frame_length_field + 1

        if len(data) < total_len:
            raise ValidationError(f"Truncated frame data: expected {total_len} bytes, got {len(data)}")

        seq_num = word3

        payload_end = total_len - (2 if has_fecf else 0)
        payload = data[5:payload_end]

        if has_fecf:
            received_crc = struct.unpack(">H", data[total_len-2:total_len])[0]
            computed_crc = calculate_ccsds_crc16(data[:payload_end])
            if received_crc != computed_crc:
                raise CRCError(f"FECF CRC mismatch! Received: 0x{received_crc:04X}, Computed: 0x{computed_crc:04X}")

        return cls(
            scid=scid,
            vcid=vcid,
            payload=payload,
            tfvn=tfvn,
            bypass=bypass,
            control=control,
            reserved=reserved,
            seq_num=seq_num,
            has_fecf=has_fecf
        )
