#!/usr/bin/env python3
"""
CCSDS Telecommand (TC) & Space Packet Processing Engine and Diagnostic Utility.

Supports CCSDS 133.0-B-2 (Space Packet Protocol) and CCSDS 232.0-B-3 (TC Transfer Frame Protocol).
Provides bidirectional packing/unpacking, fast CRC16 calculation, strict validation, and CLI diagnostics.
"""

import socket
import struct
import argparse
import sys
import binascii
import unittest
from dataclasses import dataclass
from typing import Optional, Tuple


# ==============================================================================
# Fast Table-Driven CCSDS CRC16 (Polynomial 0x1021, Initial Value 0xFFFF)
# ==============================================================================

def _generate_crc16_table() -> list[int]:
    table = []
    for i in range(256):
        curr = i << 8
        for _ in range(8):
            if curr & 0x8000:
                curr = ((curr << 1) ^ 0x1021) & 0xFFFF
            else:
                curr = (curr << 1) & 0xFFFF
        table.append(curr)
    return table

CRC16_TABLE = _generate_crc16_table()

def calculate_ccsds_crc16(data: bytes, initial: int = 0xFFFF) -> int:
    """Calculate 16-bit CCSDS CRC (CCITT-FALSE: poly 0x1021, init 0xFFFF)."""
    crc = initial
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ CRC16_TABLE[((crc >> 8) ^ byte) & 0xFF]
    return crc


class TransmissionError(Exception):
    """Raised when transmission over socket fails."""
    pass


# ==============================================================================
# Space Packet (CCSDS 133.0-B-2)
# ==============================================================================

@dataclass
class SpacePacket:
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
        if not (0 <= self.version <= 7):
            raise ValueError(f"Version must be 0-7, got {self.version}")
        if not (0 <= self.packet_type <= 1):
            raise ValueError(f"Packet type must be 0 or 1, got {self.packet_type}")
        if not (0 <= self.sec_header_flag <= 1):
            raise ValueError(f"Secondary header flag must be 0 or 1, got {self.sec_header_flag}")
        if not (0 <= self.apid <= 0x07FF):
            raise ValueError(f"APID out of 11-bit range (0-2047): {self.apid}")
        if not (0 <= self.seq_flags <= 3):
            raise ValueError(f"Sequence flags must be 0-3, got {self.seq_flags}")
        if not (0 <= self.seq_count <= 0x3FFF):
            raise ValueError(f"Sequence count out of 14-bit range (0-16383): {self.seq_count}")
        if len(self.payload) > 65536:
            raise ValueError(f"Payload length ({len(self.payload)}) exceeds max 65536 bytes")

    def pack(self) -> bytes:
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
        if len(data) < 6:
            raise ValueError(f"Data length ({len(data)}) too short for Space Packet header (min 6 bytes)")

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


# ==============================================================================
# TC Transfer Frame (CCSDS 232.0-B-3)
# ==============================================================================

@dataclass
class TCTransferFrame:
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
            raise ValueError(f"TFVN must be 0-3, got {self.tfvn}")
        if not (0 <= self.bypass <= 1):
            raise ValueError(f"Bypass flag must be 0 or 1, got {self.bypass}")
        if not (0 <= self.control <= 1):
            raise ValueError(f"Control flag must be 0 or 1, got {self.control}")
        if not (0 <= self.scid <= 0x03FF):
            raise ValueError(f"SCID out of 10-bit range (0-1023): {self.scid}")
        if not (0 <= self.vcid <= 0x3F):
            raise ValueError(f"VCID out of 6-bit range (0-63): {self.vcid}")
        if not (0 <= self.seq_num <= 0xFF):
            raise ValueError(f"Sequence number out of 8-bit range (0-255): {self.seq_num}")

    def pack(self) -> bytes:
        header_len = 5
        crc_len = 2 if self.has_fecf else 0
        total_frame_length = header_len + len(self.payload) + crc_len

        if total_frame_length > 1024:
            raise ValueError(f"Frame length {total_frame_length} exceeds CCSDS TC max 1024 bytes.")

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
            raise ValueError(f"Data length ({len(data)}) too short for TC Transfer Frame (min {min_len} bytes)")

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
            raise ValueError(f"Truncated frame data: expected {total_len} bytes, got {len(data)}")

        seq_num = word3

        payload_end = total_len - (2 if has_fecf else 0)
        payload = data[5:payload_end]

        if has_fecf:
            received_crc = struct.unpack(">H", data[total_len-2:total_len])[0]
            computed_crc = calculate_ccsds_crc16(data[:payload_end])
            if received_crc != computed_crc:
                raise ValueError(f"FECF CRC mismatch! Received: 0x{received_crc:04X}, Computed: 0x{computed_crc:04X}")

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


# ==============================================================================
# TM Transfer Frame (CCSDS 132.0-B-3)
# ==============================================================================

@dataclass
class TMTransferFrame:
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
        if not (0 <= self.tfvn <= 3):
            raise ValueError(f"TFVN must be 0-3, got {self.tfvn}")
        if not (0 <= self.scid <= 0x03FF):
            raise ValueError(f"SCID out of 10-bit range (0-1023): {self.scid}")
        if not (0 <= self.vcid <= 0x07):
            raise ValueError(f"VCID out of 3-bit range (0-7): {self.vcid}")

    def pack(self) -> bytes:
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
        min_len = 8 if has_fecf else 6
        if len(data) < min_len:
            raise ValueError(f"Data length ({len(data)}) too short for TM Transfer Frame (min {min_len} bytes)")

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
                raise ValueError(f"TM FECF CRC mismatch! Received: 0x{received_crc:04X}, Computed: 0x{computed_crc:04X}")

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


def parse_response_payload(raw_bytes: bytes) -> SpacePacket:
    """
    Attempts to unpack raw response bytes as TM Transfer Frame, TC Transfer Frame,
    or direct Space Packet. Returns extracted SpacePacket.
    """
    if not raw_bytes:
        raise ValueError("Empty response received")

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


import re

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


# ==============================================================================
# Stateful Sequence Manager (TCP / UDP Engine)
# ==============================================================================

class StatefulSession:
    """
    Stateful Engine managing multi-step CCSDS telecommand sequence interactions
    over persistent TCP or reused UDP socket connections.
    """
    def __init__(self, target_host: str, target_port: int, protocol: str = "udp",
                 scid: int = 0, vcid: int = 0, apid: int = 1,
                 bypass: int = 0, seq_num: int = 0, timeout: float = 3.0):
        self.target_host = target_host
        self.target_port = target_port
        self.protocol = protocol.lower()
        self.scid = scid
        self.vcid = vcid
        self.apid = apid
        self.bypass = bypass
        self.seq_num = seq_num
        self.timeout = timeout
        self.socket: Optional[socket.socket] = None
        self.pkt_seq_count = 0

    def connect(self):
        sock_type = socket.SOCK_DGRAM if self.protocol == "udp" else socket.SOCK_STREAM
        self.socket = socket.socket(socket.AF_INET, sock_type)
        self.socket.settimeout(self.timeout)
        if self.protocol == "tcp":
            self.socket.connect((self.target_host, self.target_port))

    def close(self):
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def send_frame(self, raw_payload: bytes) -> bytes:
        """Packs SpacePacket into TCTransferFrame, transmits, and listens for response."""
        sp = SpacePacket(
            apid=self.apid,
            payload=raw_payload,
            seq_count=self.pkt_seq_count
        )
        self.pkt_seq_count = (self.pkt_seq_count + 1) & 0x3FFF

        tc = TCTransferFrame(
            scid=self.scid,
            vcid=self.vcid,
            payload=sp.pack(),
            bypass=self.bypass,
            seq_num=self.seq_num
        )
        if self.bypass == 0:
            self.seq_num = (self.seq_num + 1) & 0xFF

        tc_bytes = tc.pack()

        if not self.socket:
            self.connect()

        if self.protocol == "tcp":
            self.socket.sendall(tc_bytes)
            resp = self.socket.recv(4096)
        else:
            self.socket.sendto(tc_bytes, (self.target_host, self.target_port))
            resp, _ = self.socket.recvfrom(4096)

        return resp

    def run_sequence(self, start_counter: int = 0, sync_payload: bytes = b"BEGIN", 
                     next_payload: bytes = b"GETFLAG", fmt_style: str = "auto") -> dict:
        """
        Executes a 2-step stateful sequence with flexible payload formatting:
        1. Transmits formatted start counter + sync_payload (e.g. 0x00:BEGIN or \x00:BEGIN)
        2. Unpacks response frame, parses returned counter & format style
        3. Transmits formatted updated counter + next_payload (e.g. 0x01:GETFLAG or \x01:GETFLAG)
        4. Unpacks final response frame and returns summary dict
        """
        active_fmt = "binary_colon" if fmt_style in ("auto", "") else fmt_style

        # Step 1: Sync
        p1 = format_counter_payload(start_counter, sync_payload, active_fmt)
        print(f"[*] [Step 1] Sending Sync Payload (Counter 0x{start_counter:02X}, Format={active_fmt}): {p1.hex().upper()} | Raw: {p1}")
        resp1_raw = self.send_frame(p1)
        print(f"[+] [Step 1] Received Raw Response ({len(resp1_raw)} bytes): {resp1_raw.hex().upper()}")

        resp1_sp = parse_response_payload(resp1_raw)
        
        if resp1_sp.payload:
            rx_counter, rx_data, detected_fmt = extract_counter_from_payload(resp1_sp.payload, start_counter)
            print(f"[+] [Step 1] Extracted Counter: 0x{rx_counter:02X} ({rx_counter}), Data: {rx_data}, Detected Format: {detected_fmt}")
            if fmt_style == "auto":
                active_fmt = detected_fmt
        else:
            rx_counter = (start_counter + 1) & 0xFF
            rx_data = b""
            print(f"[!] [Step 1] Response payload empty, auto-incrementing counter to 0x{rx_counter:02X}")

        # Step 2: Next command
        p2 = format_counter_payload(rx_counter, next_payload, active_fmt)
        print(f"[*] [Step 2] Sending Sequence Payload (Counter 0x{rx_counter:02X}, Format={active_fmt}): {p2.hex().upper()} | Raw: {p2}")
        resp2_raw = self.send_frame(p2)
        print(f"[+] [Step 2] Received Raw Response ({len(resp2_raw)} bytes): {resp2_raw.hex().upper()}")

        resp2_sp = parse_response_payload(resp2_raw)
        print(f"[+] [Step 2] Unpacked Final Payload: {resp2_sp.payload.hex().upper()} | Raw: {resp2_sp.payload}")

        return {
            "step1_sent": p1,
            "step1_resp_counter": rx_counter,
            "step1_resp_data": rx_data,
            "step2_sent": p2,
            "step2_resp_raw": resp2_raw,
            "step2_resp_payload": resp2_sp.payload if resp2_sp else None
        }




# ==============================================================================
# Network Transmission Engine
# ==============================================================================

def send_payload(target_host: str, target_port: int, protocol: str, data: bytes, 
                 timeout: float = 3.0, listen_response: bool = False) -> Optional[bytes]:
    sock_type = socket.SOCK_DGRAM if protocol.lower() == "udp" else socket.SOCK_STREAM
    response = None

    try:
        with socket.socket(socket.AF_INET, sock_type) as s:
            s.settimeout(timeout)
            if sock_type == socket.SOCK_STREAM:
                s.connect((target_host, target_port))
                s.sendall(data)
                print(f"[+] TCP transmission successful. Sent {len(data)} bytes to {target_host}:{target_port}")
                
                try:
                    response = s.recv(4096)
                    if response:
                        print(f"[+] Received TCP response ({len(response)} bytes): {response.hex().upper()}")
                except socket.timeout:
                    print("[-] No TCP response received within timeout.")
            else:
                s.sendto(data, (target_host, target_port))
                print(f"[+] UDP transmission successful. Sent {len(data)} bytes to {target_host}:{target_port}")
                
                if listen_response:
                    try:
                        response, addr = s.recvfrom(4096)
                        print(f"[+] Received UDP response from {addr[0]}:{addr[1]} ({len(response)} bytes): {response.hex().upper()}")
                    except socket.timeout:
                        print("[-] Listening timed out; no UDP response received.")

        return response

    except socket.timeout:
        print("[-] Connection/Socket timeout occurred.")
        raise TransmissionError(f"Socket operation timed out after {timeout}s")
    except Exception as e:
        print(f"[-] Transmission error: {e}")
        raise TransmissionError(str(e)) from e


# ==============================================================================
# Diagnostic Inspection & Display
# ==============================================================================

def print_frame_inspection(sp: SpacePacket, tc: TCTransferFrame, tc_bytes: bytes):
    print("\n" + "=" * 65)
    print("               CCSDS PACKET & FRAME INSPECTION")
    print("=" * 65)

    print("\n[1] Space Packet Header (CCSDS 133.0-B-2)")
    print(f"  ├─ Packet Version : {sp.version}")
    print(f"  ├─ Packet Type    : {sp.packet_type} ({'Telecommand' if sp.packet_type == 1 else 'Telemetry'})")
    print(f"  ├─ Sec Header Flag: {sp.sec_header_flag}")
    print(f"  ├─ APID           : {sp.apid} (0x{sp.apid:03X})")
    print(f"  ├─ Seq Flags      : {sp.seq_flags} (0b{sp.seq_flags:02b})")
    print(f"  ├─ Seq Counter    : {sp.seq_count}")
    print(f"  └─ Payload Size   : {len(sp.payload)} bytes")

    print("\n[2] TC Transfer Frame Header (CCSDS 232.0-B-3)")
    print(f"  ├─ TFVN           : {tc.tfvn}")
    print(f"  ├─ Bypass Flag    : {tc.bypass} ({'Expedited / Type B' if tc.bypass == 1 else 'Seq-Controlled / Type A'})")
    print(f"  ├─ Control Flag   : {tc.control} ({'Control Command' if tc.control == 1 else 'Data Frame'})")
    print(f"  ├─ SCID           : {tc.scid} (0x{tc.scid:03X})")
    print(f"  ├─ VCID           : {tc.vcid}")
    print(f"  ├─ Frame Seq Num  : {tc.seq_num}")
    print(f"  ├─ Total Length   : {len(tc_bytes)} bytes")
    print(f"  └─ FECF (CRC16)   : 0x{tc_bytes[-2:].hex().upper()}")

    print("\n[3] Hex Stream Dump")
    print(f"  └─ Raw Hex: {tc_bytes.hex().upper()}")
    print("=" * 65 + "\n")


# ==============================================================================
# Command Line Interface
# ==============================================================================

def parse_arguments():
    parser = argparse.ArgumentParser(description="CCSDS Telecommand Diagnostic Client & Frame Builder")

    parser.add_argument("-t", "--target", help="Target IP or hostname")
    parser.add_argument("-p", "--port", type=int, help="Target port")
    parser.add_argument("--proto", choices=["tcp", "udp"], default="udp", help="Transport protocol (default: udp)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Socket timeout in seconds (default: 3.0)")
    parser.add_argument("--recv", action="store_true", help="Listen for UDP response after transmission")

    # Spacecraft & Channel IDs
    parser.add_argument("--scid", type=int, default=0, help="Spacecraft Identifier (10-bit: 0-1023), default: 0")
    parser.add_argument("--vcid", type=int, default=0, help="Virtual Channel Identifier (6-bit: 0-63), default: 0")
    parser.add_argument("--apid", type=int, default=1, help="Application Process Identifier (11-bit: 0-2047), default: 1")

    # CCSDS Header Flags
    parser.add_argument("--bypass", type=int, choices=[0, 1], default=1, help="COP-1 Bypass Flag: 1=Expedited (default), 0=Sequence-Controlled")
    parser.add_argument("--seq-num", type=int, default=0, help="TC Frame Sequence Number N(S) (0-255), default: 0")
    parser.add_argument("--seq-flags", type=int, choices=[0, 1, 2, 3], default=3, help="Space Packet Sequence Flags (0=Cont, 1=First, 2=Last, 3=Unsegmented)")
    parser.add_argument("--sec-header", type=int, choices=[0, 1], default=0, help="Secondary Header Flag (0=None, 1=Present)")

    # Payload options
    parser.add_argument("--hex", action="store_true", help="Parse payload string as hex instead of raw ASCII")
    parser.add_argument("-f", "--file", help="Path to file containing payload data")
    parser.add_argument("payload", nargs="?", help="The payload string (ASCII or Hex)")

    # Execution modes
    parser.add_argument("--dry-run", action="store_true", help="Inspect packet structure without transmitting")
    parser.add_argument("--test", action="store_true", help="Run internal unit test suite")

    # Stateful Sequence Automation Options
    parser.add_argument("--auto-sequence", action="store_true", help="Run automated stateful sequence (sync -> counter ACK -> next command)")
    parser.add_argument("--start-counter", type=int, default=0, help="Initial packet sequence counter (0-255), default: 0")
    parser.add_argument("--sync-payload", default="BEGIN", help="Initial sync payload string (default: BEGIN)")
    parser.add_argument("--next-payload", default="GETFLAG", help="Follow-up payload string after ACK (default: GETFLAG)")
    parser.add_argument("--payload-format", choices=["auto", "binary_colon", "binary_raw", "hex_text", "dec_text"], default="auto", help="Payload counter formatting mode (default: auto)")

    return parser.parse_args()


# ==============================================================================
# Unit Test Suite
# ==============================================================================

class TestCCSDSEngine(unittest.TestCase):
    def test_crc16_calculation(self):
        # Verification vector for CRC16-CCITT / CCSDS
        test_data = b"123456789"
        crc = calculate_ccsds_crc16(test_data)
        self.assertEqual(crc, 0x29B1)

    def test_space_packet_pack_unpack(self):
        payload = b"\x01\x02\x03\x04\x05"
        sp = SpacePacket(apid=42, payload=payload, seq_flags=1, seq_count=100)
        packed = sp.pack()

        unpacked = SpacePacket.unpack(packed)
        self.assertEqual(unpacked.apid, 42)
        self.assertEqual(unpacked.seq_flags, 1)
        self.assertEqual(unpacked.seq_count, 100)
        self.assertEqual(unpacked.payload, payload)

    def test_tc_transfer_frame_pack_unpack(self):
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
        payload = b"TEST"
        tc = TCTransferFrame(scid=1, vcid=1, payload=payload)
        packed = bytearray(tc.pack())
        packed[-1] ^= 0xFF  # Corrupt CRC

        with self.assertRaises(ValueError):
            TCTransferFrame.unpack(bytes(packed))

    def test_out_of_range_validation(self):
        with self.assertRaises(ValueError):
            SpacePacket(apid=3000, payload=b"")
        with self.assertRaises(ValueError):
            TCTransferFrame(scid=2000, vcid=1, payload=b"")

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

    def test_stateful_session_sequence_udp(self):
        import threading
        
        def mock_server(sock):
            sock.settimeout(2.0)
            try:
                # Step 1
                data, addr = sock.recvfrom(4096)
                tc_frame = TCTransferFrame.unpack(data)
                sp = SpacePacket.unpack(tc_frame.payload)
                
                # Server responds with "0x01:ACK"
                ack_sp = SpacePacket(apid=sp.apid, payload=b"0x01:ACK")
                tm_frame = TMTransferFrame(scid=tc_frame.scid, vcid=tc_frame.vcid, payload=ack_sp.pack())
                sock.sendto(tm_frame.pack(), addr)
                
                # Step 2
                data2, addr2 = sock.recvfrom(4096)
                tc_frame2 = TCTransferFrame.unpack(data2)
                sp2 = SpacePacket.unpack(tc_frame2.payload)
                
                # Server verifies that step 2 sent "0x01:GETFLAG"
                if sp2.payload == b"0x01:GETFLAG":
                    flag_sp = SpacePacket(apid=sp2.apid, payload=b"0x02:FLAG{VALIDATED_STATE}")
                else:
                    flag_sp = SpacePacket(apid=sp2.apid, payload=b"INVALID_SEQUENCE_STATE")
                
                tm_frame2 = TMTransferFrame(scid=tc_frame2.scid, vcid=tc_frame2.vcid, payload=flag_sp.pack())
                sock.sendto(tm_frame2.pack(), addr2)
            except Exception as e:
                pass

        srv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv_sock.bind(("127.0.0.1", 0))
        port = srv_sock.getsockname()[1]
        
        t = threading.Thread(target=mock_server, args=(srv_sock,))
        t.start()

        with StatefulSession("127.0.0.1", port, protocol="udp", scid=12, vcid=4, apid=83, bypass=0) as session:
            result = session.run_sequence(start_counter=0, sync_payload=b"BEGIN", next_payload=b"GETFLAG", fmt_style="auto")
            self.assertEqual(result["step1_resp_counter"], 1)
            self.assertEqual(result["step2_resp_payload"], b"0x02:FLAG{VALIDATED_STATE}")

        srv_sock.close()
        t.join()


def run_tests():
    print("[*] Running CCSDS Engine Unit Tests...")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCCSDSEngine)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


# ==============================================================================
# Main Entry Point
# ==============================================================================

def main():
    args = parse_arguments()

    if args.test:
        run_tests()

    if args.auto_sequence:
        if not args.target or not args.port:
            print("[-] Error: --target and --port are required for --auto-sequence.")
            sys.exit(1)

        sync_bytes = binascii.unhexlify(args.sync_payload) if args.hex else args.sync_payload.encode('utf-8')
        next_bytes = binascii.unhexlify(args.next_payload) if args.hex else args.next_payload.encode('utf-8')

        print(f"[*] Starting Stateful Sequence Automation over {args.proto.upper()} to {args.target}:{args.port}...")
        print(f"[*] Config: SCID={args.scid}, VCID={args.vcid}, APID={args.apid}, Bypass={args.bypass}, PayloadFormat={args.payload_format}")

        with StatefulSession(
            target_host=args.target,
            target_port=args.port,
            protocol=args.proto,
            scid=args.scid,
            vcid=args.vcid,
            apid=args.apid,
            bypass=args.bypass,
            seq_num=args.seq_num,
            timeout=args.timeout
        ) as session:
            try:
                res = session.run_sequence(
                    start_counter=args.start_counter,
                    sync_payload=sync_bytes,
                    next_payload=next_bytes,
                    fmt_style=args.payload_format
                )
                print("\n[+] Sequence Automation Completed Successfully!")
                sys.exit(0)
            except Exception as e:
                print(f"[-] Sequence execution failed: {e}")
                sys.exit(1)

    # Process payload input
    if args.file:
        try:
            with open(args.file, "rb") as f:
                user_data = f.read()
        except OSError as e:
            print(f"[-] Error reading file '{args.file}': {e}")
            sys.exit(1)
    elif args.payload:
        try:
            if args.hex:
                user_data = binascii.unhexlify(args.payload.replace(" ", ""))
            else:
                user_data = args.payload.encode('utf-8')
        except binascii.Error as e:
            print(f"[-] Error: Payload is not valid hexadecimal: {e}")
            sys.exit(1)
    else:
        print("[-] Error: A payload string or --file input is required (unless running --test or --auto-sequence).")
        sys.exit(1)

    # Build Space Packet & TC Transfer Frame
    try:
        sp = SpacePacket(
            apid=args.apid,
            payload=user_data,
            sec_header_flag=args.sec_header,
            seq_flags=args.seq_flags
        )
        sp_bytes = sp.pack()

        tc = TCTransferFrame(
            scid=args.scid,
            vcid=args.vcid,
            payload=sp_bytes,
            bypass=args.bypass,
            seq_num=args.seq_num
        )
        tc_bytes = tc.pack()
    except ValueError as e:
        print(f"[-] Encoding error: {e}")
        sys.exit(1)

    # Display inspection report
    print_frame_inspection(sp, tc, tc_bytes)

    if args.dry_run:
        print("[*] Dry-run mode enabled. Skipping transmission.")
        return

    # Validate target network settings
    if not args.target or not args.port:
        print("[-] Error: --target and --port are required for transmission (or use --dry-run).")
        sys.exit(1)

    print(f"[*] Transmitting over {args.proto.upper()} to {args.target}:{args.port}...")
    try:
        send_payload(
            target_host=args.target,
            target_port=args.port,
            protocol=args.proto,
            data=tc_bytes,
            timeout=args.timeout,
            listen_response=args.recv
        )
    except TransmissionError:
        sys.exit(1)


if __name__ == "__main__":
    main()