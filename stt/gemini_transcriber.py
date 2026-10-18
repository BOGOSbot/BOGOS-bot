"""Transcribe a whole chunk through Gemini's audio input.

Not the /audio/transcriptions endpoint. Gemini takes audio as a content part in
a normal chat request, which means the prompt travels with the audio: saying it
is two players mid-game of Warhammer 40,000 is what makes the model spell the
unit names the way the table did. A dedicated ASR endpoint gets no such hint and
writes what it heard instead, which on this session produced "battle shop".

A chunk can come back as its opening sentence. The model reports finish_reason
"stop" after six completion tokens and the rest of the audio is simply not
written down; the lite tier did it to four of six shapings of two recordings,
and which shapes it does it to is stable per input but not predictable from the
audio. Nothing downstream can tell a six-word reading of five minutes from a
quiet five minutes, so the reading is checked here, where the audio length is
known, and re-asked when it cannot be right.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from config import audio_cfg, stt_cfg
from stt.chunker import pcm_to_wav
from utils.logging import get_logger

logger = get_logger(__name__)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# Cloudflare-fronted routes refuse a bare urllib signature.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Below this, the reading is too thin to be the audio it came from. French
# table talk measures 1.5 to 3 words per second, and a chunk the chunker built
# from VAD-selected speech cannot legitimately sit under 0.8. Measured failures
# came in at 0.04 (24s -> "Ok.") and 0.15 (33s -> one sentence).
MIN_WORDS_PER_SECOND = 0.8
MIN_WORDS = 3

# A whole chunk answered with a clock is the other way this model fails: a
# 300s chunk came back as "00:00" through "04:59", one label per second, and
# not one word of speech.
_TIMESTAMPS = re.compile(r"^(\d{1,2}:\d{2}\s*)+$")

# Appended on the second attempt. The first prompt says what not to do; this
# one says what to do, which is the instruction the early stop ignores.
RETRY_SUFFIX = " Transcribe every sentence, from the first word to the last."


def looks_complete(text: str, seconds: float) -> bool:
    """Could this be a full reading of that much audio?"""
    stripped = text.strip()
    if not stripped or _TIMESTAMPS.match(stripped):
        return False
    return len(stripped.split()) >= max(MIN_WORDS, MIN_WORDS_PER_SECOND * seconds)


def tidy(text: str) -> str:
    """Give back the paragraphing the model forgot.

    It usually separates utterances with a blank line and sometimes with a
    single newline, which reads as one wall in Discord. Both are the same
    transcript; make them look the same.
    """
    body = re.sub(r"\n{3,}", "\n\n", text.strip())
    if "\n\n" not in body and body.count("\n") >= 3:
        body = body.replace("\n", "\n\n")
    return body


class GeminiTranscriber:
    """One request per chunk, with the prompt that carries the context."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or stt_cfg.chunk_model
        logger.info("Chunk transcriber ready: %s", self._model)

    @property
    def model(self) -> str:
        return self._model

    def transcribe(self, user_id: int, pcm: bytes) -> dict[str, Any] | None:
        seconds = len(pcm) / (
            audio_cfg.output_sample_rate * audio_cfg.sample_width
        )
        attempts = max(1, stt_cfg.chunk_attempts)
        best: dict[str, Any] | None = None

        for attempt in range(1, attempts + 1):
            prompt = stt_cfg.chunk_prompt
            if attempt > 1:
                prompt += RETRY_SUFFIX
            result = self._ask(prompt, pcm)
            if result is None:
                continue
            if best is None or len(result["text"]) > len(best["text"]):
                best = result
            if looks_complete(result["text"], seconds):
                best["attempts"] = attempt
                return best
            logger.warning(
                "Chunk of %.0fs read back as %d word(s) (attempt %d/%d): %s",
                seconds,
                len(result["text"].split()),
                attempt,
                attempts,
                result["text"][:70],
            )

        if best is not None:
            best["attempts"] = attempts
            logger.error(
                "Gave up completing a %.0fs chunk after %d attempt(s); "
                "keeping the longest reading, %d word(s).",
                seconds,
                attempts,
                len(best["text"].split()),
            )
        return best

    def _ask(self, prompt: str, pcm: bytes) -> dict[str, Any] | None:
        body = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": stt_cfg.chunk_max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
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
        text = tidy(text or "")
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
