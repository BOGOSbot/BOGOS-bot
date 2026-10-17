"""Turn two readings into one transcript the reader can trust.

The bot transcribes every utterance twice, with two models, and reconciles at
word level as it goes. That mechanical pass settles the differences that are
spelling: a number written out or in digits, a plural, a name spelled two ways,
a word one model cut in half. What it cannot settle is everything else, and
those differences come in three kinds:

  * the same meaning said differently, where either reading will do;
  * a fabricated reading, where one model produced something nobody said;
  * a genuine dispute, where the two disagree about what was heard.

An LLM settles the first two and is told to leave the third alone, with both
readings kept. It never hears the audio, so it is never asked which reading is
right. That is deliberate: the wrong reading is usually the fluent one, so a
model that answers confidently is exactly the failure this design exists to
avoid. Ambiguity is reported, not resolved.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from utils.logging import get_logger

logger = get_logger(__name__)

SYSTEM = """You reconcile two automatic transcriptions of the same recording of
French tabletop gaming. Each numbered line is one utterance. A bracket like
(a | b) means the two models heard different words: "a" is model A, "b" is
model B. Text outside a bracket is agreed by both and must never change.

For each line, decide one of three things:

SAME - the two readings mean the same thing. Keep one. Prefer the reading that
uses the vocabulary of the surrounding lines and the register of a game being
played: slang, numbers read as played, unit names.

FABRICATED - one reading is not speech from this conversation. Signals: an
encyclopedic sentence about a date, a government, a museum, a prize, a
celebrity; a brand name nobody would say here; a word that does not exist in
French; a foreign-language phrase out of nowhere. Keep the other reading and
drop the fabrication.

UNSURE - you cannot tell from the text which was said. Keep BOTH readings in the
bracket, unchanged. Do not guess. A reader will decide with the audio.

Rules:
- Never invent words. Never paraphrase. Never merge two lines.
- Never drop a line unless every word in it came from a fabrication.
- Keep the speaker's register, including slang, hesitation and grammatical
  errors that a speaker would make.
- If a line has no bracket, do not return it.

Answer with JSON only, no prose:
{"lines": [{"n": <line number>, "text": "<the final line, brackets removed where
you settled them, brackets kept where you were unsure>"}]}
Return text "" only when the whole line is fabricated.
"""


@dataclass
class Reconciled:
    """The LLM's verdict on the disputed lines."""

    replacements: dict[int, str] = field(default_factory=dict)
    error: str = ""


def _payload(model: str, numbered: list[str]) -> dict:
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "\n".join(numbered)},
        ],
        "response_format": {"type": "json_object"},
    }


# Measured on this endpoint: about five seconds per line. Twelve lines keeps a
# call near a minute, which is short enough that one slow batch cannot eat the
# whole session budget.
BATCH = 8

# The endpoint is intermittent: a small batch answers in ten seconds, and the
# same size occasionally stalls past a minute. A per-call timeout alone lets one
# stalled batch cost the whole transcript, because the post waits for every
# batch to finish, so the pass has a deadline as well and gives up on the rest.
BUDGET = 300.0


def reconcile(
    numbered: list[str],
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 75.0,
    budget: float = BUDGET,
) -> Reconciled:
    """Settle the disputed lines, in batches small enough to answer."""
    deadline = time.monotonic() + budget
    merged: dict[int, str] = {}
    left_over = 0
    for start in range(0, len(numbered), BATCH):
        part = numbered[start:start + BATCH]
        remaining = deadline - time.monotonic()
        if remaining < 15:
            left_over = len(numbered) - start
            break
        result = _once(part, base_url, api_key, model, min(timeout, remaining))
        if result.error and deadline - time.monotonic() > 20:
            result = _once(
                part, base_url, api_key, model, min(timeout, deadline - time.monotonic())
            )
        merged.update(result.replacements)
        if result.error:
            logger.warning(
                "Gave up on %d line(s) at %d: %s", len(part), start, result.error
            )
    if left_over:
        logger.warning(
            "Reconciliation budget spent; %d line(s) stay disputed.", left_over
        )
    return Reconciled(replacements=merged)


def _once(
    numbered: list[str],
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 180.0,
) -> Reconciled:
    """Ask the model to settle the disputed lines.

    numbered: one string per line, already prefixed with its line number.
    Returns replacements keyed by line number. On any failure the dict is empty
    and the caller keeps the mechanical reconciliation, which is safe: the
    brackets stay, the reader sees both readings, nothing is lost.
    """
    if not numbered:
        return Reconciled()
    if not api_key:
        return Reconciled(error="no API key for the reconciler")

    url = base_url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(_payload(model, numbered)).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        logger.error("Reconciler failed (HTTP %s): %s", exc.code, detail)
        return Reconciled(error="HTTP %s" % exc.code)
    except Exception as exc:
        logger.error("Reconciler error: %s", exc)
        return Reconciled(error=str(exc)[:120])

    try:
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.error("Reconciler returned something unreadable: %s", exc)
        return Reconciled(error="unreadable response")

    out: dict[int, str] = {}
    for item in parsed.get("lines") or []:
        try:
            number = int(item["n"])
        except (KeyError, TypeError, ValueError):
            continue
        text = item.get("text")
        if isinstance(text, str):
            out[number] = text.strip()
    logger.info("Reconciler settled %d line(s).", len(out))
    return Reconciled(replacements=out)


def parse_numbered(line: str) -> tuple[int, str] | None:
    """Split "12. body" into (12, "body")."""
    match = re.match(r"^\s*(\d+)\.\s?(.*)$", line)
    if not match:
        return None
    return int(match.group(1)), match.group(2)
