"""The voice gate: an utterance must contain someone talking into a mic.

Measured on the 26 Oct meeting, the hallucinated lines came from clips whose
average level looked like quiet speech but which held a click or a breath over
room noise, or other people leaking out of someone's speakers. Counting the
frames that are actually loud separates the two; the average does not.
"""

from __future__ import annotations

import numpy as np

from stt.processor import _dbfs, _voiced_seconds

RATE = 16000


def _tone(seconds: float, dbfs: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    amplitude = 32768 * 10 ** (dbfs / 20) * np.sqrt(2)
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.int16)


def test_speech_at_a_mic_counts_in_full():
    assert abs(_voiced_seconds(_tone(2.0, -20), -45) - 2.0) < 0.05


def test_a_quiet_room_counts_for_nothing():
    assert _voiced_seconds(_tone(5.0, -55), -45) == 0.0


def test_a_click_on_noise_is_not_speech_even_when_its_average_is():
    """One loud 40 ms click in 5 s of hiss averages above the old -60 floor."""
    clip = _tone(5.0, -55)
    clip[RATE : RATE + 640] = _tone(0.04, -3)
    assert _dbfs(clip) > -60
    assert _voiced_seconds(clip, -45) < 0.3


def test_a_short_real_reply_passes():
    assert _voiced_seconds(_tone(0.5, -25), -45) >= 0.3


def test_an_empty_clip_is_silent():
    assert _voiced_seconds(np.zeros(0, dtype=np.int16), -45) == 0.0
