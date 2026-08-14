def generate_ccsds_sequence(length_bytes):
    seq = bytearray()
    state = 0xFF
    for _ in range(length_bytes):
        byte_val = 0
        for i in range(8):
            bit = state & 1
            byte_val = (byte_val << 1) | bit
            new_bit = ((state >> 0) ^ (state >> 3) ^ (state >> 5) ^ (state >> 7)) & 1
            state = (state >> 1) | (new_bit << 7)
        seq.append(byte_val)
    return bytes(seq)

def randomize_ccsds(data: bytes) -> bytes:
    seq = generate_ccsds_sequence(len(data))
    return bytes(a ^ b for a, b in zip(data, seq))
