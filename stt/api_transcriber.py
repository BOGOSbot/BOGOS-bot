"""
Cloud speech-to-text backend over OpenRouter's /audio/transcriptions endpoint.

Mirrors the local Transcriber interface so the processor can swap backends with
a config flag. Note this endpoint is NOT OpenAI-compatible: the body is JSON
carrying base64 audio under input_audio, not multipart/form-data.
"""

from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
import wave
from typing import Any

from config import api_cfg, audio_cfg, stt_cfg
from utils.logging import get_logger

logger = get_logger(__name__)

ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"


def pcm_to_wav_bytes(pcm: bytes) -> bytes:
    """Wrap raw 16 kHz mono int16 PCM in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(audio_cfg.output_channels)
        w.setsampwidth(audio_cfg.sample_width)
        w.setframerate(audio_cfg.output_sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class ApiTranscriber:
    """Transcribes one utterance per request through OpenRouter."""

    def __init__(self, model: str | None = None) -> None:
        if not api_cfg.openrouter_key:
            raise RuntimeError(
                "STT_BACKEND=openrouter requires OPENROUTER_API_KEY in .env"
            )
        self._key = api_cfg.openrouter_key
        # The second opinion is the same client with another model id, so it
        # inherits the request shape instead of duplicating it.
        self._model = model or api_cfg.model
        logger.info("Cloud STT backend ready: %s", self._model)

    @property
    def model(self) -> str:
        return self._model

    def transcribe(self, user_id: int, audio_bytes: bytes) -> dict[str, Any] | None:
        if len(audio_bytes) < stt_cfg.min_audio_bytes:
            return None

        payload: dict[str, Any] = {
            "model": self._model,
            "input_audio": {
                "data": base64.b64encode(pcm_to_wav_bytes(audio_bytes)).decode("ascii"),
                "format": "wav",
            },
        }
        language = stt_cfg.language.strip().lower()
        if language and language != "auto":
            payload["language"] = language

        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self._key,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=api_cfg.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            logger.error("STT request failed (HTTP %s): %s", exc.code, detail)
            return None
        except Exception as exc:
            logger.error("STT request error: %s", exc)
            return None

        text = (data.get("text") or "").strip()
        if not text:
            return None

        elapsed = time.perf_counter() - t0
        cost = (data.get("usage") or {}).get("cost")
        logger.debug("Cloud STT user %s in %.2fs (cost %s)", user_id, elapsed, cost)
        return {"user_id": user_id, "text": text, "latency": f"{elapsed:.3f}s"}
