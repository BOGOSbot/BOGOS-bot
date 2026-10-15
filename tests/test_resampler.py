import numpy as np
import pytest

from audio.resampler import AudioResampler, StreamingResampler


def test_streaming_resampler_preserves_a_passband_tone() -> None:
    orig, new = 48000, 16000
    tone = np.sin(2 * np.pi * 440 * np.arange(orig) / orig)
    r = StreamingResampler(orig, new)
    out = np.concatenate([r.process(tone[i:i + 960]) for i in range(0, len(tone), 960)])
    # Pre-history counts as silence so the stream starts at sample 0, but a
    # streaming filter holds back its last few outputs pending more input
    # (taps/ratio samples, about 0.6 ms here).
    assert abs(len(out) - new) <= r.taps // r.ratio + 4
    mid = out[len(out) // 4: -len(out) // 4]
    rms = float(np.sqrt((mid ** 2).mean()))
    assert 0.9 < rms / (2 ** -0.5) < 1.1


def test_block_size_does_not_change_the_result() -> None:
    """The whole point: a packet boundary must not perturb the output."""
    tone = np.sin(2 * np.pi * 300 * np.arange(48000) / 48000)
    a, b = StreamingResampler(48000, 16000), StreamingResampler(48000, 16000)
    out_a = np.concatenate([a.process(tone[i:i + 960]) for i in range(0, len(tone), 960)])
    out_b = np.concatenate([b.process(tone[i:i + 100]) for i in range(0, len(tone), 100)])
    n = min(len(out_a), len(out_b))
    assert np.allclose(out_a[:n], out_b[:n], atol=1e-9)


def test_audio_resampler_emits_16khz_mono_int16_bytes() -> None:
    r = AudioResampler()
    total = 0
    for _ in range(10):
        out = r.resample(np.zeros((960, 2), dtype=np.int16).tobytes())
        assert isinstance(out, bytes)
        total += len(out)
    # 10 x 20 ms at 48 kHz stereo -> about 10 x 20 ms at 16 kHz mono.
    assert abs(total - 10 * 320 * 2) <= 64
