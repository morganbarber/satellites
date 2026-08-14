"""
Low-level single-shot socket transmission functionality.
"""

import socket
from typing import Optional

try:
    import zmq
    HAS_ZMQ = True
except ImportError:
    HAS_ZMQ = False

from ccsds.exceptions import TransmissionError
from ccsds.transport.afsk import modulate_afsk, demodulate_afsk


def send_payload(target_host: str, target_port: int, protocol: str, data: bytes,
                 timeout: float = 3.0, listen_response: bool = False, afsk: bool = False) -> Optional[bytes]:
    """
    Transmits packed CCSDS data over TCP, UDP, or ZMQ socket.
    Optionally listens for a response frame.
    """
    protocol = protocol.lower()

    if afsk:
        data = modulate_afsk(data)
        
    response = None

    try:
        if protocol == "zmq":
            if not HAS_ZMQ:
                raise TransmissionError("PyZMQ module is required for zmq protocol.")
            context = zmq.Context()
            sock = context.socket(zmq.REQ)
            sock.setsockopt(zmq.RCVTIMEO, int(timeout * 1000))
            sock.setsockopt(zmq.SNDTIMEO, int(timeout * 1000))
            sock.connect(f"tcp://{target_host}:{target_port}")
            sock.send(data)
            print(f"[+] ZMQ transmission successful. Sent {len(data)} bytes to tcp://{target_host}:{target_port}")
            
            if listen_response:
                try:
                    response = sock.recv()
                    print(f"[+] Received ZMQ response ({len(response)} bytes).")
                except zmq.error.Again:
                    print("[-] No ZMQ response received within timeout.")
            
            sock.close()
            context.term()
        else:
            sock_type = socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM
            with socket.socket(socket.AF_INET, sock_type) as s:
                s.settimeout(timeout)
                if sock_type == socket.SOCK_STREAM:
                    s.connect((target_host, target_port))
                    s.sendall(data)
                    print(f"[+] TCP transmission successful. Sent {len(data)} bytes to {target_host}:{target_port}")
    
                    try:
                        response = s.recv(65536)
                        if response:
                            print(f"[+] Received TCP response ({len(response)} bytes).")
                    except socket.timeout:
                        print("[-] No TCP response received within timeout.")
                else:
                    s.sendto(data, (target_host, target_port))
                    print(f"[+] UDP transmission successful. Sent {len(data)} bytes to {target_host}:{target_port}")
    
                    if listen_response:
                        try:
                            response, addr = s.recvfrom(65536)
                            print(
                                f"[+] Received UDP response from {addr[0]}:{addr[1]} "
                                f"({len(response)} bytes)."
                            )
                        except socket.timeout:
                            print("[-] Listening timed out; no UDP response received.")

        if response and afsk:
            response = demodulate_afsk(response)

        return response

    except socket.timeout as exc:
        print("[-] Connection/Socket timeout occurred.")
        raise TransmissionError(f"Socket operation timed out after {timeout}s") from exc
    except Exception as e:
        print(f"[-] Transmission error: {e}")
        raise TransmissionError(str(e)) from e
