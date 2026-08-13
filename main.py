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
        print("[-] Error: A payload string or --file input is required (unless running --test).")
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