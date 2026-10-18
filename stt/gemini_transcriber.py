"""Transcribe a whole chunk through Gemini's audio input.

Not the /audio/transcriptions endpoint. Gemini takes audio as a content part in
a normal chat request, which means the prompt travels with the audio: saying it
is two players mid-game of Warhammer 40,000 is what makes the model spell the
unit names the way the table did. A dedicated ASR endpoint gets no such hint and
writes what it heard instead, which on this session produced "battle shop".

A chunk can come back as its opening sentence. The model reports finish_reason
"stop" after six completion tokens and the rest of the audio is simply not
written down. Which chunks it does that to is stable per input but not
predictable from the audio, and it is not a size problem: sweeping the same
195 dumps at 30, 60, 120 and 300 seconds left a different passage missing at
each size, and every one of them was recovered by asking about part of the
chunk instead. The same audio asked the same way stops in the same place.

So the reading is checked here, where the audio length is known, and re-asked
twice over: once with a prompt that says what to do rather than what not to do,
and if that fails, in halves, recursively.
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

# Halving past this is not worth it: the halves stop being utterances and start
# being fragments, and a fragment with no speech in it is what the model answers
# with invented text.
MIN_SPLIT_SECONDS = 20.0
MAX_SPLIT_DEPTH = 3

# Sentence ends, for putting the paragraphing back. Guarded so it does not fire
# on a decimal ("3.7") or an initial ("M. Dupont"): a lower-case letter or a
# digit before the stop, a capital after it.
_SENTENCE_END = re.compile(
    r"(?<=[a-z\u00e0-\u00f6\u00f8-\u00ff0-9])([.!?])\s+(?=[A-Z\u00c0-\u00d6\u00d8-\u00de])"
)

# What one block of transcript should weigh when the model hands back a wall.
# 60-second chunks came back at 87 words a block and read well; five-minute ones
# at 617 and read as one slab.
WALL_BLOCK_WORDS = 80


def looks_complete(text: str, seconds: float) -> bool:
    """Could this be a full reading of that much audio?"""
    stripped = text.strip()
    if not stripped or _TIMESTAMPS.match(stripped):
        return False
    return len(stripped.split()) >= max(MIN_WORDS, MIN_WORDS_PER_SECOND * seconds)


def _blocks(text: str) -> str:
    """Regroup a wall of text into readable paragraphs at sentence ends."""
    sentences = [s.strip() for s in _SENTENCE_END.sub(r"\1\n", text).split("\n") if s.strip()]
    out, current, size = [], [], 0
    for sentence in sentences:
        current.append(sentence)
        size += len(sentence.split())
        if size >= WALL_BLOCK_WORDS:
            out.append(" ".join(current))
            current, size = [], 0
    if current:
        out.append(" ".join(current))
    return "\n\n".join(out)


def tidy(text: str) -> str:
    """Give back the paragraphing the model forgot.

    It usually separates utterances with a blank line and sometimes with a
    single newline, and sometimes with nothing at all: one paragraph for a
    whole chunk, which in Discord is a slab and in a file is worse. The first
    two are the same transcript spelled differently; the third is regrouped at
    sentence ends so the reader gets blocks instead of a wall.
    """
    body = re.sub(r"\n{3,}", "\n\n", text.strip())
    if "\n\n" in body:
        return body
    if body.count("\n") >= 3:
        return body.replace("\n", "\n\n")
    return _blocks(body)


def _halves(pcm: bytes) -> tuple[bytes, bytes]:
    """Split at the silence nearest the middle, so no word is cut in two.

    The chunker puts 0.4s of silence between utterances; the middle of a chunk
    is usually inside one of those gaps or inside a word. Prefer the gap.
    """
    frame = audio_cfg.sample_width
    middle = (len(pcm) // 2) & ~1
    window = int(0.2 * audio_cfg.output_sample_rate) * frame
    best, best_gap = middle, 0
    start = max(0, middle - int(15.0 * audio_cfg.output_sample_rate) * frame)
    end = min(len(pcm), middle + int(15.0 * audio_cfg.output_sample_rate) * frame)
    run = 0
    for offset in range(start, end - frame, frame):
        if pcm[offset:offset + frame] == b"\x00\x00":
            run += frame
            if run > best_gap:
                best_gap = run
                best = offset + frame
        else:
            run = 0
    if best_gap < window:
        best = middle
    best &= ~1
    return pcm[:best], pcm[best:]


class GeminiTranscriber:
    """One request per chunk, with the prompt that carries the context."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or stt_cfg.chunk_model
        logger.info("Chunk transcriber ready: %s", self._model)

    @property
    def model(self) -> str:
        return self._model

    def transcribe(
        self, user_id: int, pcm: bytes, depth: int = 0
    ) -> dict[str, Any] | None:
        seconds = len(pcm) / (
            audio_cfg.output_sample_rate * audio_cfg.sample_width
        )
        best = self._read_until_complete(pcm, seconds)
        if best is not None and looks_complete(best["text"], seconds):
            return best

        if depth >= MAX_SPLIT_DEPTH or seconds < MIN_SPLIT_SECONDS:
            if best is not None:
                logger.error(
                    "Gave up completing a %.0fs chunk; keeping the longest "
                    "reading, %d word(s).",
                    seconds,
                    len(best["text"].split()),
                )
            return best

        started = time.perf_counter()
        pieces = [
            self.transcribe(user_id, half, depth + 1)
            for half in _halves(pcm)
        ]
        pieces = [piece for piece in pieces if piece]
        if not pieces:
            return best

        text = " ".join(piece["text"] for piece in pieces)
        recovered = {
            "text": text,
            "latency": round(time.perf_counter() - started, 3),
            "cost": sum(piece.get("cost") or 0.0 for piece in pieces),
            "attempts": sum(piece.get("attempts") or 1 for piece in pieces),
            "split": True,
        }
        if looks_complete(text, seconds) or best is None or len(text) > len(best["text"]):
            logger.warning(
                "Chunk of %.0fs was abandoned; recovered %d word(s) by asking "
                "about its halves.",
                seconds,
                len(text.split()),
            )
            return recovered
        return best

    def _read_until_complete(self, pcm: bytes, seconds: float) -> dict[str, Any] | None:
        """Ask the whole chunk, then ask it again differently."""
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
