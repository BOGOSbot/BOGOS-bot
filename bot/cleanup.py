"""
LLM post-pass over a finished transcript.

Errors that survive a good ASR model are usually resolvable from context: a
misheard homophone, a name, a number. This sends the raw lines to a text
model and asks for corrections only, never a rewrite.

It never edits silently: the result is rendered as a word-level diff, with
removals struck through and additions in italics, and only the changed lines
are shown. The verbatim transcript stays in the channel above.
"""

from __future__ import annotations

import difflib
import json
import urllib.error
import urllib.request
from typing import Optional

from config import cleanup_cfg
from utils.logging import get_logger

logger = get_logger(__name__)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = (
    "You are a careful post-editor for French speech-recognition transcripts.\n"
    "You receive a JSON array of transcript lines, in order.\n"
    "Fix ONLY clear recognition errors, using the surrounding lines as context: "
    "misheard words, homophones, numbers, proper nouns, and missing or wrong "
    "punctuation.\n"
    "Rules:\n"
    "- Never paraphrase, summarise, translate, reorder, merge or split lines.\n"
    "- Never change the meaning, and never add anything that was not said.\n"
    "- If a line is already plausible, return it completely unchanged.\n"
    "- Keep the original language: French stays French.\n"
    "Reply with JSON only, of the form {\"lines\": [...]}, with exactly the "
    "same number of entries. Each entry is the corrected TEXT of that line, "
    "with no speaker prefix."
)


def clean_lines(lines: list[str]) -> Optional[list[str]]:
    """Ask the model to correct ASR errors. None when unavailable or unusable."""
    if not lines or not cleanup_cfg.openrouter_key:
        return None

    payload = {
        "model": cleanup_cfg.model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps({"lines": lines}, ensure_ascii=False),
            },
        ],
    }
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + cleanup_cfg.openrouter_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=cleanup_cfg.timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        corrected = json.loads(content).get("lines")
    except urllib.error.HTTPError as exc:
        logger.error(
            "Cleanup failed (HTTP %s): %s",
            exc.code,
            exc.read().decode("utf-8", "replace")[:200],
        )
        return None
    except Exception as exc:
        logger.error("Cleanup request error: %s", exc)
        return None

    if not isinstance(corrected, list) or len(corrected) != len(lines):
        logger.warning(
            "Cleanup returned %s entries for %d lines; discarding.",
            len(corrected) if isinstance(corrected, list) else "no",
            len(lines),
        )
        return None
    return [str(item) for item in corrected]


def render_diff(original: str, corrected: str) -> str:
    """Word-level diff: removals struck through, additions in italics."""
    before, after = original.split(), corrected.split()
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left = " ".join(before[i1:i2])
        right = " ".join(after[j1:j2])
        if tag == "equal":
            parts.append(left)
        elif tag == "delete":
            parts.append(f"~~{left}~~")
        elif tag == "insert":
            parts.append(f"*{right}*")
        else:
            parts.append(f"~~{left}~~ *{right}*")
    rendered = " ".join(part for part in parts if part)
    return rendered or original
