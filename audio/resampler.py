"""
High-quality streaming audio resampler for the Discord -> speech pipeline.

Converts 48 kHz stereo int16 PCM (Discord's decoded Opus output) into
16 kHz mono int16 PCM (what speech models want).

Why this is not a one-liner: resampling each 20 ms Discord packet on its own
gives the anti-aliasing filter no history, so every packet boundary produces a
transient. Measured on real speech that cost roughly 27-34 dB of SNR against a
transparent resample, and on one sample pushed WER from 33% to 46%. This
implementation carries filter state across packets and decimates by an integer
factor with a windowed-sinc FIR.

Stateful means ONE INSTANCE PER AUDIO STREAM. Several speakers interleaved
through a single instance would splice unrelated audio together.
"""

from __future__ import annotations

import time

import numpy as np

from config import audio_cfg
from utils.logging import get_logger

logger = get_logger(__name__)

# Reset the filter history after this long without audio from a speaker, so a
# stale filter does not smear the first word of their next sentence.
_IDLE_RESET_SECONDS = 1.0


def design_lowpass(taps: int, cutoff_hz: float, sample_rate: int) -> np.ndarray:
    """Kaiser-windowed sinc low-pass, normalised to unity DC gain."""
    n = np.arange(taps, dtype=np.float64) - (taps - 1) / 2.0
    h = np.sinc(2.0 * cutoff_hz / sample_rate * n) * np.kaiser(taps, 8.0)
    return h / h.sum()


class StreamingResampler:
    """Integer-factor decimator that carries filter state between blocks."""

    def __init__(
        self,
        orig_freq: int = audio_cfg.input_sample_rate,
        new_freq: int = audio_cfg.output_sample_rate,
        taps: int = 63,
    ) -> None:
        if orig_freq % new_freq != 0:
            raise ValueError(
                f"streaming resampler needs an integer ratio, got {orig_freq}->{new_freq}"
            )
        self.ratio = orig_freq // new_freq
        self.taps = taps
        self.delay = (taps - 1) // 2
        self.filter = design_lowpass(taps, new_freq / 2.0, orig_freq)

        # Seed with the filter's delay as silence, so output sample 0 can be
        # produced. Without this the stream starts shifted by delay/ratio
        # samples, which decorrelates the first fraction of a second.
        self._buf = np.zeros(self.delay, dtype=np.float64)
        self._buf_start = -self.delay  # absolute input index of _buf[0]
        self._next_out = 0             # next output sample index to emit

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Feed mono float samples; get back whatever is now fully determined."""
        if samples.size == 0:
            return np.zeros(0, dtype=np.float64)

        self._buf = np.concatenate([self._buf, samples.astype(np.float64)])
        buf_end = self._buf_start + self._buf.size  # exclusive

        # Output k needs inputs [ratio*k - delay, ratio*k - delay + taps).
        k0 = max(self._next_out, -(-(self._buf_start + self.delay) // self.ratio))
        k1 = (buf_end - self.taps + self.delay) // self.ratio
        if k1 < k0:
            return np.zeros(0, dtype=np.float64)

        ks = np.arange(k0, k1 + 1, dtype=np.int64)
        base = ks * self.ratio - self.delay - self._buf_start
        idx = base[:, None] + np.arange(self.taps, dtype=np.int64)[None, :]
        out = self._buf[idx] @ self.filter
        self._next_out = int(k1) + 1

        # Drop input that no future output can ever reach.
        keep_from = max(0, self._next_out * self.ratio - self.delay - self._buf_start)
        if keep_from > 0:
            self._buf = self._buf[keep_from:]
            self._buf_start += keep_from
        return out


class AudioResampler:
    """One stateful 48 kHz stereo -> 16 kHz mono converter."""

    # Create one per speaker and call resample() for each of that speaker's
    # Discord packets, in order. See the module docstring.

    def __init__(self) -> None:
        self._resampler = StreamingResampler()
        self._last_seen = time.monotonic()

    def idle_for(self) -> float:
        """Seconds since this stream last received audio."""
        return time.monotonic() - self._last_seen

    def resample(self, pcm_bytes: bytes) -> bytes:
        now = time.monotonic()
        if now - self._last_seen > _IDLE_RESET_SECONDS:
            self._resampler = StreamingResampler()
        self._last_seen = now

        stereo = np.frombuffer(pcm_bytes, dtype=np.int16)
        if stereo.size < audio_cfg.input_channels:
            return b""

        frames = stereo.reshape(-1, audio_cfg.input_channels)
        mono = frames.mean(axis=1).astype(np.float64)

        out = self._resampler.process(mono)
        if out.size == 0:
            return b""

        clipped = np.clip(np.rint(out), -32768, 32767).astype(np.int16)
        return clipped.tobytes()
