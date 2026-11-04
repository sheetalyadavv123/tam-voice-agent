import numpy as np

from bridge.audio import CHUNK_BYTES, pcm16_to_ulaw, ulaw_to_pcm16


def test_chunk_is_20ms():
    assert CHUNK_BYTES == 160


def test_round_trip_is_close():
    pcm = (np.sin(np.linspace(0, 200, 8000)) * 20000).astype(np.int16)
    back = ulaw_to_pcm16(pcm16_to_ulaw(pcm))
    assert np.max(np.abs(back.astype(int) - pcm.astype(int))) < 1100


def test_silence_stays_silent():
    back = ulaw_to_pcm16(pcm16_to_ulaw(np.zeros(160, np.int16)))
    assert np.all(np.abs(back) <= 8)