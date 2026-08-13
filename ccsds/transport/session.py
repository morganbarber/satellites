"""
Stateful session engine for multi-step CCSDS telecommand interactions.
"""

import socket
from typing import Optional

from ccsds.models.space_packet import SpacePacket
from ccsds.models.tc_frame import TCTransferFrame
from ccsds.transport.payload import (
    extract_counter_from_payload,
    format_counter_payload,
    parse_response_payload,
)


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
        """Establishes network socket connection and flushes TCP banners if present."""
        sock_type = socket.SOCK_DGRAM if self.protocol == "udp" else socket.SOCK_STREAM
        self.socket = socket.socket(socket.AF_INET, sock_type)
        self.socket.settimeout(self.timeout)
        if self.protocol == "tcp":
            self.socket.connect((self.target_host, self.target_port))
            # Flush initial TCP connection banners if present
            try:
                self.socket.settimeout(0.3)
                banner_data = b""
                while True:
                    try:
                        chunk = self.socket.recv(1024)
                        if not chunk:
                            break
                        banner_data += chunk
                    except (socket.timeout, BlockingIOError):
                        break
                if banner_data:
                    clean_banner = banner_data.decode('utf-8', errors='ignore').strip()
                    print(f"[*] Flushed TCP connection banner ({len(banner_data)} bytes): {clean_banner}")
            except OSError:
                pass
            finally:
                self.socket.settimeout(self.timeout)

    def close(self):
        """Closes active network socket connection if present."""
        if self.socket:
            try:
                self.socket.close()
            except OSError:
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
        3. Transmits formatted updated counter + next_payload (e.g. 0x02:GETFLAG or \x02:GETFLAG)
        4. Unpacks final response frame and returns summary dict
        """
        active_fmt = "hex_text" if fmt_style in ("auto", "") else fmt_style

        # Step 1: Sync
        p1 = format_counter_payload(start_counter, sync_payload, active_fmt)
        print(f"[*] [Step 1] Sending Sync Payload (Counter 0x{start_counter:02X}, Format={active_fmt}): "
              f"{p1.hex().upper()} | Raw: {p1}")
        resp1_raw = self.send_frame(p1)
        print(f"[+] [Step 1] Received Raw Response ({len(resp1_raw)} bytes): {resp1_raw.hex().upper()}")

        resp1_sp = parse_response_payload(resp1_raw)

        if resp1_sp.payload:
            rx_counter, rx_data, detected_fmt = extract_counter_from_payload(resp1_sp.payload, start_counter)
            print(f"[+] [Step 1] Extracted Counter: 0x{rx_counter:02X} ({rx_counter}), "
                  f"Data: {rx_data}, Detected Format: {detected_fmt}")
            if fmt_style == "auto":
                active_fmt = detected_fmt
            # Next ground counter increments from the spacecraft's returned ACK counter
            next_counter = (rx_counter + 1) & 0xFF
        else:
            rx_counter = (start_counter + 1) & 0xFF
            next_counter = (rx_counter + 1) & 0xFF
            rx_data = b""
            print(f"[!] [Step 1] Response payload empty, auto-incrementing counter to 0x{next_counter:02X}")

        # Step 2: Next command
        p2 = format_counter_payload(next_counter, next_payload, active_fmt)
        print(f"[*] [Step 2] Sending Sequence Payload (Counter 0x{next_counter:02X}, Format={active_fmt}): "
              f"{p2.hex().upper()} | Raw: {p2}")
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
