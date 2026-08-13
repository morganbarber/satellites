"""
CLI visual diagnostic inspection formatting.
"""

from ccsds.models.space_packet import SpacePacket
from ccsds.models.tc_frame import TCTransferFrame


def print_frame_inspection(sp: SpacePacket, tc: TCTransferFrame, tc_bytes: bytes):
    """Prints detailed visual header & field breakdown for Space Packet and TC Frame."""
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
    bypass_str = 'Expedited / Type B' if tc.bypass == 1 else 'Seq-Controlled / Type A'
    print(f"  ├─ Bypass Flag    : {tc.bypass} ({bypass_str})")
    print(f"  ├─ Control Flag   : {tc.control} ({'Control Command' if tc.control == 1 else 'Data Frame'})")
    print(f"  ├─ SCID           : {tc.scid} (0x{tc.scid:03X})")
    print(f"  ├─ VCID           : {tc.vcid}")
    print(f"  ├─ Frame Seq Num  : {tc.seq_num}")
    print(f"  ├─ Total Length   : {len(tc_bytes)} bytes")
    print(f"  └─ FECF (CRC16)   : 0x{tc_bytes[-2:].hex().upper()}")

    print("\n[3] Hex Stream Dump")
    print(f"  └─ Raw Hex: {tc_bytes.hex().upper()}")
    print("=" * 65 + "\n")
