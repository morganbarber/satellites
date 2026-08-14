import io
import math
import struct
import wave

def modulate_afsk(data: bytes, sample_rate: int = 48000, baud: int = 1200) -> bytes:
    """Modulates binary data into AFSK1200 16-bit PCM raw audio bytes."""
    samples_per_bit = sample_rate // baud
    
    # Extract bits (MSB first)
    bits = []
    for b in data:
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
            
    audio_samples = []
    phase = 0.0
    
    for bit in bits:
        freq = 1200.0 if bit == 1 else 2200.0
        phase_inc = 2.0 * math.pi * freq / sample_rate
        for _ in range(samples_per_bit):
            sample = math.sin(phase)
            audio_samples.append(sample)
            phase += phase_inc
            if phase > 2.0 * math.pi:
                phase -= 2.0 * math.pi
                
    pcm_bytes = bytearray()
    for s in audio_samples:
        # Scale to 16-bit signed integer
        val = int(s * 32767.0)
        pcm_bytes.extend(struct.pack("<h", val))
        
    return bytes(pcm_bytes)

def demodulate_afsk(audio_data: bytes, sample_rate: int = 48000, baud: int = 1200) -> bytes:
    """Demodulates AFSK1200 16-bit PCM audio bytes back into binary data."""
    if not audio_data:
        return b""
        
    # Check if it's a WAV file or raw PCM
    if audio_data.startswith(b"RIFF"):
        try:
            buf = io.BytesIO(audio_data)
            with wave.open(buf, 'rb') as f:
                params = f.getparams()
                nframes = params.nframes
                nchannels = params.nchannels
                sampwidth = params.sampwidth
                raw_frames = f.readframes(nframes)
                
            if sampwidth == 2:
                fmt = f"<{nframes * nchannels}h"
                samples = struct.unpack(fmt, raw_frames)
                samples = samples[0::nchannels]
            else:
                return b""
        except wave.Error:
            return b""
    else:
        # Assume raw 16-bit PCM mono
        n_samples = len(audio_data) // 2
        fmt = f"<{n_samples}h"
        try:
            samples = struct.unpack(fmt, audio_data)
        except struct.error:
            return b""

    if not samples:
        return b""

    samples_float = [s / 32768.0 for s in samples]
    
    # Delay and multiply discriminator
    k = 7 # 90 degree phase shift for 1700 Hz
    demod = []
    for i in range(k, len(samples_float)):
        demod.append(samples_float[i] * samples_float[i-k])
        
    if not demod:
        return b""

    # Low-pass filter
    ma_len = sample_rate // baud
    lpf = []
    
    if len(demod) < ma_len:
        s_sum = sum(demod)
        lpf.append(s_sum / len(demod) if len(demod) > 0 else 0)
    else:
        s_sum = sum(demod[:ma_len])
        lpf.append(s_sum / ma_len)
        for i in range(ma_len, len(demod)):
            s_sum += demod[i] - demod[i-ma_len]
            lpf.append(s_sum / ma_len)
            
    # Pad to align signal
    lpf = [0]*k + [lpf[0]]*(ma_len//2) + lpf + [lpf[-1]]*(ma_len - ma_len//2 - 1)
    
    # Zero crossings
    zero_crossings = []
    for i in range(1, len(lpf)):
        if (lpf[i-1] >= 0 and lpf[i] < 0) or (lpf[i-1] < 0 and lpf[i] >= 0):
            zero_crossings.append(i)
            
    # Clock recovery
    sampled_tones = []
    if len(zero_crossings) == 0:
        return b""
        
    next_center = zero_crossings[0] + ma_len // 2
    while next_center < len(lpf):
        sampled_tones.append(1 if lpf[int(next_center)] > 0 else 0)
        
        expected_transition = next_center + ma_len // 2
        best_zc = -1
        min_dist = ma_len // 4
        for zc in zero_crossings:
            dist = abs(zc - expected_transition)
            if dist < min_dist:
                min_dist = dist
                best_zc = zc
                
        if best_zc != -1:
            next_center = best_zc + ma_len // 2
        else:
            next_center += ma_len
            
    # Tones to bits (MSB first)
    bit_string = "".join([str(t) for t in sampled_tones])
    
    out_bytes = bytearray()
    for i in range(0, len(bit_string) - 7, 8):
        b = bit_string[i:i+8]
        val = int(b, 2)
        out_bytes.append(val)
        
    return bytes(out_bytes)
