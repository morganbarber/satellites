import math
import struct
import wave
import io

def modulate_afsk(data: bytes, sample_rate: int = 48000, baud: int = 1200) -> bytes:
    """
    Modulates raw bytes into AFSK1200 16-bit PCM WAV audio.
    Mark (1) = 1200 Hz, Space (0) = 2200 Hz.
    """
    samples_per_bit = sample_rate // baud
    
    # Convert data to MSB-first bits
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
                
    # Convert samples to 16-bit little-endian PCM
    pcm = bytearray()
    for s in audio_samples:
        val = int(s * 32767.0)
        # Ensure it fits in 16-bit signed integer
        if val > 32767: val = 32767
        elif val < -32768: val = -32768
        pcm.extend(struct.pack("<h", val))
        
    # Wrap in WAV
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
        
    return buf.getvalue()


def demodulate_afsk(wav_data: bytes, sample_rate: int = 48000, baud: int = 1200) -> bytes:
    """
    Demodulates AFSK1200 16-bit PCM WAV audio or raw PCM into raw bytes.
    """
    if not wav_data:
        return b""
        
    try:
        buf = io.BytesIO(wav_data)
        with wave.open(buf, 'rb') as f:
            params = f.getparams()
            nframes = params.nframes
            data = f.readframes(nframes)
            
            if params.sampwidth == 2:
                fmt = f"<{nframes*params.nchannels}h"
                samples = struct.unpack(fmt, data)
                # Take left channel if stereo
                samples = samples[0::params.nchannels]
            else:
                return b""
    except wave.Error:
        # Fallback to raw PCM if wave header is missing or corrupted
        if len(wav_data) % 2 != 0:
            return b""
        nframes = len(wav_data) // 2
        fmt = f"<{nframes}h"
        samples = struct.unpack(fmt, wav_data)

    samples = [s / 32768.0 for s in samples]

    # Delay and multiply discriminator
    k = 7 # 90 degree phase shift for 1700 Hz (center between 1200 and 2200 Hz at 48000 Hz)
    demod = []
    for i in range(k, len(samples)):
        demod.append(samples[i] * samples[i-k])

    # Low-pass filter (moving average)
    ma_len = sample_rate // baud
    lpf = []
    
    if len(demod) < ma_len:
        return b""
        
    s_sum = sum(demod[:ma_len])
    lpf.append(s_sum / ma_len)
    for i in range(ma_len, len(demod)):
        s_sum += demod[i] - demod[i-ma_len]
        lpf.append(s_sum / ma_len)

    # Pad to align signal
    lpf = [0]*k + [lpf[0]]*(ma_len//2) + lpf + [lpf[-1]]*(ma_len - ma_len//2 - 1)

    # Find zero crossings to recover clock
    zero_crossings = []
    for i in range(1, len(lpf)):
        if (lpf[i-1] >= 0 and lpf[i] < 0) or (lpf[i-1] < 0 and lpf[i] >= 0):
            zero_crossings.append(i)

    # Clock recovery and sampling
    sampled_tones = []
    if not zero_crossings:
        return b""
        
    next_center = zero_crossings[0] + ma_len // 2
    while next_center < len(lpf):
        sampled_tones.append(1 if lpf[int(next_center)] > 0 else 0)
        
        # Resynchronize clock to the closest zero crossing
        expected_transition = next_center + ma_len // 2
        best_zc = -1
        min_dist = max(10, ma_len // 4)
        for zc in zero_crossings:
            dist = abs(zc - expected_transition)
            if dist < min_dist:
                min_dist = dist
                best_zc = zc
                
        if best_zc != -1:
            next_center = best_zc + ma_len // 2
        else:
            next_center += ma_len

    bit_string = "".join(str(t) for t in sampled_tones)
    
    out_bytes = bytearray()
    for i in range(0, len(bit_string) - 7, 8):
        b = bit_string[i:i+8]
        val = int(b, 2)
        out_bytes.append(val)
        
    return bytes(out_bytes)
