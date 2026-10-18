"""Transcribe a whole chunk through Gemini's audio input.

Not the /audio/transcriptions endpoint. Gemini takes audio as a content part in
a normal chat request, which means the prompt travels with the audio: saying it
is two players mid-game of Warhammer 40,000 is what makes the model spell the
unit names the way the table did. A dedicated ASR endpoint gets no such hint and
writes what it heard instead, which on this session produced "battle shop".
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from typing import Any

from config import stt_cfg
from stt.chunker import pcm_to_wav
from utils.logging import get_logger

logger = get_logger(__name__)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# Cloudflare-fronted routes refuse a bare urllib signature.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class GeminiTranscriber:
    """One request per chunk, with the prompt that carries the context."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or stt_cfg.chunk_model
        logger.info("Chunk transcriber ready: %s", self._model)

    @property
    def model(self) -> str:
        return self._model

    def transcribe(self, user_id: int, pcm: bytes) -> dict[str, Any] | None:
        body = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": stt_cfg.chunk_max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": stt_cfg.chunk_prompt},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": base64.b64encode(pcm_to_wav(pcm)).decode(
                                    "ascii"
                                ),
                                "format": "wav",
                            },
                        },
                    ],
                }
            ],
        }
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + stt_cfg.api_key,
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/BOGOSbot/BOGOS-bot",
                "X-Title": "BOGOS bot",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                request, timeout=stt_cfg.chunk_timeout_seconds
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            logger.error("Chunk transcription failed (HTTP %s): %s", exc.code, detail)
            return None
        except Exception as exc:
            logger.error("Chunk transcription error: %s", exc)
            return None

        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
        text = (text or "").strip()
        usage = data.get("usage") or {}
        if not text:
            logger.warning(
                "Chunk came back empty (usage %s).", usage.get("prompt_tokens")
            )
            return None
        return {
            "text": text,
            "latency": round(time.perf_counter() - started, 3),
            "cost": usage.get("cost"),
        }
