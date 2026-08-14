def encode_nrzm(bits):
    nrzm_bits = []
    current = 0
    for bit in bits:
        if bit == 1:
            nrzm_bits.append(current) # 1 = no transition
        else:
            current = 1 - current     # 0 = transition
            nrzm_bits.append(current)
    return nrzm_bits
