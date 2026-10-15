"""
Discord AudioSink that captures voice data, resamples it,
and forwards it to the STT process via IPC queue.
"""

from __future__ import annotations

import queue
import time
from multiprocessing import Queue as MPQueue
from typing import Optional, Union

import discord
from discord.ext.voice_recv import AudioSink, VoiceData

from audio.resampler import AudioResampler
from utils.logging import get_logger

logger = get_logger(__name__)


class STTAudioSink(AudioSink):
    """
    Receives 48 kHz stereo PCM from Discord, resamples to
    16 kHz mono, and pushes (user_id, pcm_bytes) into the queue.
    """

    def __init__(self, audio_queue: MPQueue) -> None:
        super().__init__()
        self._queue = audio_queue
        # ONE RESAMPLER PER SPEAKER. The resampler carries filter state, and
        # packets from different speakers arrive interleaved; sharing a single
        # instance would splice unrelated audio together.
        self._resamplers: dict[int, AudioResampler] = {}
        self._dropped_frames = 0
        self._last_drop_log = 0.0
        logger.info("STTAudioSink initialised.")

    def wants_opus(self) -> bool:
        """We want decoded PCM, not raw Opus."""
        return False

    def write(
        self,
        user: Optional[Union[discord.User, discord.Member]],
        data: VoiceData,
    ) -> None:
        if user is None:
            return
        try:
            resampler = self._resamplers.get(user.id)
            if resampler is None:
                resampler = AudioResampler()
                self._resamplers[user.id] = resampler
                self._prune_resamplers()
            resampled = resampler.resample(data.pcm)
            if not resampled:
                return  # still filling the filter; nothing to send yet
            # Carry the display name alongside the audio. Resolving it later
            # depends on discord.py's member cache, which stays empty without
            # the privileged members intent -- that is what produced "User <id>".
            name = (
                getattr(user, "display_name", None)
                or getattr(user, "name", None)
                or str(user.id)
            )
            self._queue.put_nowait((user.id, name, resampled))
        except queue.Full:
            self._dropped_frames += 1
            now = time.monotonic()
            if now - self._last_drop_log >= 5:
                logger.warning(
                    "Audio queue full; dropped %d frames in the last %.1fs.",
                    self._dropped_frames,
                    now - self._last_drop_log if self._last_drop_log else 0.0,
                )
                self._dropped_frames = 0
                self._last_drop_log = now
        except Exception as exc:
            logger.error("AudioSink write error: %s", exc)

    def _prune_resamplers(self) -> None:
        """Drop filter state for speakers long gone, keeping the map bounded."""
        if len(self._resamplers) <= 32:
            return
        stale = [uid for uid, r in self._resamplers.items() if r.idle_for() > 120.0]
        for uid in stale:
            del self._resamplers[uid]

    def cleanup(self) -> None:
        self._resamplers.clear()
        logger.info("STTAudioSink cleanup.")
