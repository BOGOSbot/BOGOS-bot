"""Write a session's audio to disk, per speaker, for building a corpus.

The per-utterance dump (STT_DUMP_DIR) is already exactly what the model
receives, but it is cut by the current VAD settings. A corpus needs the audio
*before* that decision, so segmentation can be re-tried, thresholds re-tuned
and models compared on identical material without recapturing any of it.

One continuous WAV per speaker at the pipeline's own rate, plus a session.json
naming the participants. Off unless RECORD_DIR is set.

This records other people's voices. It is opt-in for that reason.
"""

from __future__ import annotations

import json
import re
import wave
from datetime import datetime, timezone
from pathlib import Path

from utils.logging import get_logger

logger = get_logger(__name__)

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def _safe(name: str) -> str:
    """A display name is free text; make it safe for a filename."""
    cleaned = _UNSAFE.sub("_", name).strip("_")
    return cleaned[:40] or "speaker"


class ChannelRecorder:
    """Per-speaker WAVs for one session. A no-op when given no directory."""

    def __init__(self, directory: str, sample_rate: int = 16_000) -> None:
        self._root = Path(directory) if directory and directory.strip() else None
        self._rate = sample_rate
        self._started = datetime.now(timezone.utc)
        self._files: dict[int, wave.Wave_write] = {}
        self._names: dict[int, str] = {}

    @property
    def enabled(self) -> bool:
        return self._root is not None

    def write(self, user_id: int, name: str, pcm: bytes) -> None:
        """Append one chunk of one speaker's audio."""
        if self._root is None:
            return
        try:
            handle = self._files.get(user_id)
            if handle is None:
                handle = self._open(user_id, name)
            handle.writeframes(pcm)
        except Exception:
            # A corpus is optional; the transcript is not. Give up quietly
            # rather than failing the audio path or spamming the log.
            logger.exception("Channel recording failed; disabled for this session.")
            self._root = None
            self.close()

    def _open(self, user_id: int, name: str) -> wave.Wave_write:
        assert self._root is not None
        self._root.mkdir(parents=True, exist_ok=True)
        handle = wave.open(str(self._root / self._stem(user_id, name)), "wb")
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(self._rate)
        self._files[user_id] = handle
        self._names[user_id] = name
        logger.info("Recording %s into %s", name, self._root)
        return handle

    def _stem(self, user_id: int, name: str) -> str:
        return "%s__%s__%s.wav" % (
            self._started.strftime("%Y%m%d-%H%M%S"),
            user_id,
            _safe(name),
        )

    def close(self) -> None:
        """Finalise every WAV and write the session index. Idempotent."""
        for handle in self._files.values():
            try:
                handle.close()
            except Exception:
                logger.exception("Could not close a recording cleanly.")
        if self._files and self._root is not None:
            self._write_index()
        self._files.clear()

    def _write_index(self) -> None:
        assert self._root is not None
        payload = {
            "started": self._started.isoformat(),
            "sample_rate": self._rate,
            "channels": 1,
            "sample_width": 2,
            "speakers": {str(uid): name for uid, name in self._names.items()},
        }
        path = self._root / (
            self._started.strftime("%Y%m%d-%H%M%S") + "__session.json"
        )
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        logger.info("Recording index written: %s", path.name)
