"""
CCSDS Telecommand & Telemetry Processing Package.

Supports CCSDS 133.0-B-2 (Space Packet Protocol),
CCSDS 232.0-B-3 (TC Transfer Frame Protocol), and
CCSDS 132.0-B-3 (TM Transfer Frame Protocol).
"""

from ccsds.crc import calculate_ccsds_crc16
from ccsds.exceptions import (
    CCSDSError,
    CRCError,
    PayloadError,
    TransmissionError,
    ValidationError,
)
from ccsds.models import SpacePacket, TCTransferFrame, TMTransferFrame
from ccsds.transport import PersistentConsole, StatefulSession, send_payload

__version__ = "1.0.0"

__all__ = [
    "calculate_ccsds_crc16",
    "CCSDSError",
    "ValidationError",
    "CRCError",
    "TransmissionError",
    "PayloadError",
    "SpacePacket",
    "TCTransferFrame",
    "TMTransferFrame",
    "send_payload",
    "StatefulSession",
    "PersistentConsole",
]
