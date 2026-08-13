"""
Payload parsing, counter extraction, and formatting utilities.
"""

import re
from typing import Tuple

from ccsds.exceptions import PayloadError
from ccsds.models.space_packet import SpacePacket
from ccsds.models.tc_frame import TCTransferFrame
from ccsds.models.tm_frame import TMTransferFrame


def parse_response_payload(raw_bytes: bytes) -> SpacePacket:
    """
    Attempts to unpack raw response bytes as TM Transfer Frame, TC Transfer Frame,
    or direct Space Packet. Returns extracted SpacePacket.
    """
    if not raw_bytes:
        raise PayloadError("Empty response received")

    # 1. Try TM Transfer Frame
    try:
        tm_frame = TMTransferFrame.unpack(raw_bytes)
        return SpacePacket.unpack(tm_frame.payload)
    except Exception:
        pass

    # 2. Try TC Transfer Frame
    try:
        tc_frame = TCTransferFrame.unpack(raw_bytes)
        return SpacePacket.unpack(tc_frame.payload)
    except Exception:
        pass

    # 3. Try Space Packet directly
    try:
        return SpacePacket.unpack(raw_bytes)
    except Exception:
        pass

    # 4. Fallback: Wrap raw bytes into diagnostic SpacePacket payload
    return SpacePacket(apid=0, payload=raw_bytes)


def extract_counter_from_payload(payload_bytes: bytes, current_counter: int) -> Tuple[int, bytes, str]:
    """
    Extracts the updated counter integer, data portion, and detected format style
    from a response payload bytes object.
    
    Supported formats:
    - hex_text:     b"0x01:ACK" or b"0x01 ACK" -> counter=1, style="hex_text"
    - dec_text:     b"1:ACK" or b"1 ACK"       -> counter=1, style="dec_text"
    - binary_colon: b"\x01:ACK"                -> counter=1, style="binary_colon"
    - binary_raw:   b"\x01ACK"                 -> counter=1, style="binary_raw"
    """
    if not payload_bytes:
        return ((current_counter + 1) & 0xFF, b"", "binary_colon")

    # Try ASCII text decoding first
    try:
        text = payload_bytes.decode('utf-8', errors='ignore').strip()
        
        # Match "0x01:ACK" or "0x01 ACK" or "0x01"
        hex_match = re.search(r'0x([0-9a-fA-F]{1,2})\b(?:[:\s]*(.*))?', text)
        if hex_match:
            cnt = int(hex_match.group(1), 16)
            data = (hex_match.group(2) or "").encode('utf-8')
            return (cnt & 0xFF, data, "hex_text")
            
        # Match "1:ACK" or "1 ACK" or "1" (where decimal integer is at start)
        dec_match = re.search(r'^(\d{1,3})(?:[:\s]+(.*))?$', text)
        if dec_match:
            cnt = int(dec_match.group(1))
            data = (dec_match.group(2) or "").encode('utf-8')
            return (cnt & 0xFF, data, "dec_text")
    except Exception:
        pass

    # Check binary with colon: byte 0 is counter, byte 1 is ASCII ':' (0x3A)
    if len(payload_bytes) >= 2 and payload_bytes[1] == 0x3A:
        cnt = payload_bytes[0]
        data = payload_bytes[2:]
        return (cnt & 0xFF, data, "binary_colon")

    # Check binary raw: byte 0 is counter
    if len(payload_bytes) >= 1:
        cnt = payload_bytes[0]
        data = payload_bytes[1:]
        return (cnt & 0xFF, data, "binary_raw")

    # Fallback if unparseable
    return ((current_counter + 1) & 0xFF, payload_bytes, "binary_colon")


def format_counter_payload(counter: int, command: bytes, fmt_style: str = "binary_colon") -> bytes:
    """
    Formats a counter and command payload into byte array matching the specified format style.
    
    Styles:
    - "binary_colon": bytes([counter]) + b":" + command
    - "binary_raw":   bytes([counter]) + command
    - "hex_text":     b"0x00:BEGIN" (or b"0x00:" + command)
    - "dec_text":     b"0:BEGIN" (or b"0:" + command)
    """
    cmd_str = command.decode('utf-8', errors='ignore') if isinstance(command, bytes) else str(command)
    
    if fmt_style == "binary_colon":
        return bytes([counter & 0xFF]) + b":" + command
    elif fmt_style == "binary_raw":
        return bytes([counter & 0xFF]) + command
    elif fmt_style == "hex_text":
        return f"0x{counter & 0xFF:02X}:{cmd_str}".encode('utf-8')
    elif fmt_style == "dec_text":
        return f"{counter & 0xFF}:{cmd_str}".encode('utf-8')
    else:
        # Default to binary_colon
        return bytes([counter & 0xFF]) + b":" + command
