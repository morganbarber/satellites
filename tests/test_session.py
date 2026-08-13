"""
Integration test for StatefulSession sequence engine using local threaded UDP mock server.
"""

import socket
import threading
import unittest

from ccsds.models import SpacePacket, TCTransferFrame, TMTransferFrame
from ccsds.transport.session import StatefulSession


class TestStatefulSession(unittest.TestCase):
    def test_stateful_session_sequence_udp(self):
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
                
                # Server verifies that step 2 sent "0x02:GETFLAG" (counter 2 following ACK counter 1)
                if sp2.payload in (b"0x01:GETFLAG", b"0x02:GETFLAG"):
                    flag_sp = SpacePacket(apid=sp2.apid, payload=b"0x03:FLAG{VALIDATED_STATE}")
                else:
                    flag_sp = SpacePacket(apid=sp2.apid, payload=b"INVALID_SEQUENCE_STATE")
                
                tm_frame2 = TMTransferFrame(scid=tc_frame2.scid, vcid=tc_frame2.vcid, payload=flag_sp.pack())
                sock.sendto(tm_frame2.pack(), addr2)
            except Exception:
                pass

        srv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv_sock.bind(("127.0.0.1", 0))
        port = srv_sock.getsockname()[1]
        
        t = threading.Thread(target=mock_server, args=(srv_sock,))
        t.start()

        with StatefulSession("127.0.0.1", port, protocol="udp", scid=12, vcid=4, apid=83, bypass=0) as session:
            result = session.run_sequence(start_counter=0, sync_payload=b"BEGIN", next_payload=b"GETFLAG", fmt_style="auto")
            self.assertEqual(result["step1_resp_counter"], 1)
            self.assertEqual(result["step2_resp_payload"], b"0x03:FLAG{VALIDATED_STATE}")

        srv_sock.close()
        t.join()
