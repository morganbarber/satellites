"""
Fast table-driven CCSDS 16-bit CRC algorithm (Polynomial 0x1021, Initial 0xFFFF).
"""

def _generate_crc16_table() -> list[int]:
    """Precompute CRC16 lookup table for byte-by-byte processing."""
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
    """
    Calculate 16-bit CCSDS CRC (CCITT-FALSE: polynomial 0x1021, initial value 0xFFFF).
    """
    crc = initial
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ CRC16_TABLE[((crc >> 8) ^ byte) & 0xFF]
    return crc
