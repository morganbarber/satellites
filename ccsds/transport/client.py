"""
Low-level single-shot socket transmission functionality.
"""

import socket
from typing import Optional

from ccsds.exceptions import TransmissionError
from ccsds.transport.afsk import modulate_afsk, demodulate_afsk


def send_payload(target_host: str, target_port: int, protocol: str, data: bytes,
                 timeout: float = 3.0, listen_response: bool = False,
                 afsk: bool = False) -> Optional[bytes]:
    """
    Transmits packed CCSDS data over TCP or UDP socket.
    Optionally listens for a response frame.
    """
    sock_type = socket.SOCK_DGRAM if protocol.lower() == "udp" else socket.SOCK_STREAM
    response = None

    try:
        if afsk:
            data = modulate_afsk(data)
            
        with socket.socket(socket.AF_INET, sock_type) as s:
            s.settimeout(timeout)
            if sock_type == socket.SOCK_STREAM:
                s.connect((target_host, target_port))
                s.sendall(data)
                print(f"[+] TCP transmission successful. Sent {len(data)} bytes to {target_host}:{target_port}")

                try:
                    response = s.recv(65536)
                    if response:
                        if afsk:
                            response = demodulate_afsk(response)
                        print(f"[+] Received TCP response ({len(response)} bytes): {response.hex().upper()}")
                except socket.timeout:
                    print("[-] No TCP response received within timeout.")
            else:
                s.sendto(data, (target_host, target_port))
                print(f"[+] UDP transmission successful. Sent {len(data)} bytes to {target_host}:{target_port}")

                if listen_response:
                    try:
                        response, addr = s.recvfrom(65536)
                        if response and afsk:
                            response = demodulate_afsk(response)
                        print(
                            f"[+] Received UDP response from {addr[0]}:{addr[1]} "
                            f"({len(response)} bytes): {response.hex().upper()}"
                        )
                    except socket.timeout:
                        print("[-] Listening timed out; no UDP response received.")

        return response

    except socket.timeout as exc:
        print("[-] Connection/Socket timeout occurred.")
        raise TransmissionError(f"Socket operation timed out after {timeout}s") from exc
    except Exception as e:
        print(f"[-] Transmission error: {e}")
        raise TransmissionError(str(e)) from e
