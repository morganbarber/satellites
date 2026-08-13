"""
Space Packet Protocol (CCSDS 133.0-B-2).
"""

import struct
from dataclasses import dataclass
from ccsds.exceptions import ValidationError


@dataclass
class SpacePacket:
    """
    Represents a CCSDS Space Packet (CCSDS 133.0-B-2).
    """
    apid: int
    payload: bytes
    version: int = 0
    packet_type: int = 1       # 0 = Telemetry, 1 = Telecommand
    sec_header_flag: int = 0   # 0 = Not Present, 1 = Present
    seq_flags: int = 3         # 0 = Continuation, 1 = First, 2 = Last, 3 = Unsegmented
    seq_count: int = 0         # 14-bit sequence counter (0 - 16383)

    def __post_init__(self):
        self.validate()

    def validate(self):
        """Validates header fields and payload boundaries."""
        if not 0 <= self.version <= 7:
            raise ValidationError(f"Version must be 0-7, got {self.version}")
        if not 0 <= self.packet_type <= 1:
            raise ValidationError(f"Packet type must be 0 or 1, got {self.packet_type}")
        if not 0 <= self.sec_header_flag <= 1:
            raise ValidationError(f"Secondary header flag must be 0 or 1, got {self.sec_header_flag}")
        if not 0 <= self.apid <= 0x07FF:
            raise ValidationError(f"APID out of 11-bit range (0-2047): {self.apid}")
        if not 0 <= self.seq_flags <= 3:
            raise ValidationError(f"Sequence flags must be 0-3, got {self.seq_flags}")
        if not 0 <= self.seq_count <= 0x3FFF:
            raise ValidationError(f"Sequence count out of 14-bit range (0-16383): {self.seq_count}")
        if len(self.payload) > 65536:
            raise ValidationError(f"Payload length ({len(self.payload)}) exceeds max 65536 bytes")

    def pack(self) -> bytes:
        """Packs SpacePacket object into raw binary bytes."""
        word1 = ((self.version & 0x07) << 13) | \
                ((self.packet_type & 0x01) << 12) | \
                ((self.sec_header_flag & 0x01) << 11) | \
                (self.apid & 0x07FF)

        word2 = ((self.seq_flags & 0x03) << 14) | (self.seq_count & 0x3FFF)

        data_length = max(len(self.payload) - 1, 0)
        header = struct.pack(">HHH", word1, word2, data_length)
        return header + self.payload

    @classmethod
    def unpack(cls, data: bytes) -> "SpacePacket":
        """Unpacks raw binary bytes into a SpacePacket instance."""
        if len(data) < 6:
            raise ValidationError(f"Data length ({len(data)}) too short for Space Packet header (min 6 bytes)")

        word1, word2, data_length = struct.unpack(">HHH", data[:6])

        version = (word1 >> 13) & 0x07
        packet_type = (word1 >> 12) & 0x01
        sec_header_flag = (word1 >> 11) & 0x01
        apid = word1 & 0x07FF

        seq_flags = (word2 >> 14) & 0x03
        seq_count = word2 & 0x3FFF

        expected_payload_len = data_length + 1
        payload = data[6:6 + expected_payload_len]

        return cls(
            version=version,
            packet_type=packet_type,
            sec_header_flag=sec_header_flag,
            apid=apid,
            seq_flags=seq_flags,
            seq_count=seq_count,
            payload=payload
        )
