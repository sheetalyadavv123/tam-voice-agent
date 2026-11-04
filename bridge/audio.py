"""Phone audio for SAMPARK: mu-law, 8 kHz, 20 ms chunks, base64."""
import base64

import numpy as np

SAMPLE_RATE = 8000
CHUNK_MS = 20
CHUNK_BYTES = SAMPLE_RATE * CHUNK_MS // 1000  # 160 mu-law bytes = 20 ms

BIAS = 0x84
CLIP = 32635


def ulaw_to_pcm16(data: bytes) -> np.ndarray:
    u = (~np.frombuffer(data, dtype=np.uint8)).astype(np.int32)
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = (((mantissa << 3) + BIAS) << exponent) - BIAS
    return np.where(sign != 0, -sample, sample).astype(np.int16)


def pcm16_to_ulaw(pcm) -> bytes:
    s = np.asarray(pcm, dtype=np.int32)
    sign = np.where(s < 0, 0x80, 0).astype(np.int32)
    mag = np.minimum(np.abs(s), CLIP) + BIAS
    exponent = np.clip(np.floor(np.log2(mag)).astype(np.int32) - 7, 0, 7)
    mantissa = (mag >> (exponent + 3)) & 0x0F
    u = (~(sign | (exponent << 4) | mantissa)) & 0xFF
    return u.astype(np.uint8).tobytes()


def b64_to_ulaw(payload: str) -> bytes:
    return base64.b64decode(payload)


def ulaw_to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def chunks(data: bytes, size: int = CHUNK_BYTES):
    for i in range(0, len(data), size):
        yield data[i:i + size]