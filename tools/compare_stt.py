"""Run the same audio through several STT models and report cost and agreement.

The point is not to crown a winner. It is to make the disagreements visible,
because that is where the meaning is: a model that quietly swaps a proper noun
for a common word costs exactly the same as one that gets it right, and a
benchmark on clean read speech cannot see the difference.

Two models that disagree are also two independent witnesses. This prints the
disagreements in a form a human can adjudicate by ear.

    python tools/compare_stt.py <wav> [<wav> ...] [--models a,b]
"""

from __future__ import annotations

import argparse
import base64
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import api_cfg  # noqa: E402  -- loads .env and the key

ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_MODELS = ["openai/gpt-transcribe", "mistralai/voxtral-small-24b-2507"]


def transcribe(path: Path, model: str, language: str) -> dict:
    """One request. Returns text, latency in seconds, and what it cost."""
    payload = {
        "model": model,
        "input_audio": {
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            "format": "wav",
        },
        "usage": {"include": True},  # so the response carries the real cost
    }
    if language and language != "auto":
        payload["language"] = language

    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_cfg.openrouter_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        return {"text": "", "error": "HTTP %s %s" % (exc.code, detail), "latency": 0.0, "cost": None}
    except Exception as exc:
        return {"text": "", "error": str(exc)[:200], "latency": 0.0, "cost": None}
    elapsed = time.perf_counter() - started

    usage = data.get("usage") or {}
    return {
        "text": (data.get("text") or "").strip(),
        "error": None,
        "latency": elapsed,
        "cost": usage.get("cost"),
        "seconds": usage.get("seconds"),
    }


def words(text: str) -> list[str]:
    """A comparison unit: words without punctuation, lowercased."""
    return re.findall(r"[\w\u00c0-\u017f]+", text.lower())


def disagreements(a: str, b: str) -> list[str]:
    """The phrases where two transcripts part company."""
    left, right = words(a), words(b)
    matcher = difflib.SequenceMatcher(None, left, right)
    out = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        out.append("%s -> %s" % (" ".join(left[i1:i2]) or "(nothing)", " ".join(right[j1:j2]) or "(nothing)"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wavs", nargs="+")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--language", default="fr")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--reuse", default="")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]

    # Results already paid for, keyed by (file, model). Adding a model to a
    # run must not re-buy the ones already measured.
    reuse = {}
    if args.reuse and Path(args.reuse).is_file():
        for row in json.loads(Path(args.reuse).read_text(encoding="utf-8")):
            for name, r in row.get("models", {}).items():
                reuse[(row["file"], name)] = r
    results = []
    total_cost = 0.0
    total_seconds = 0.0

    for wav in args.wavs:
        path = Path(wav)
        if not path.is_file():
            print("skip (missing): %s" % wav)
            continue
        import wave
        with wave.open(str(path), "rb") as w:
            duration = w.getnframes() / w.getframerate()
        total_seconds += duration

        print("")
        print("=" * 78)
        print("%s   %.1f s" % (path.name, duration))
        print("=" * 78)
        row = {"file": path.name, "duration": duration, "models": {}}

        for model in models:
            r = reuse.get((path.name, model))
            if r is None:
                r = transcribe(path, model, args.language)
            else:
                print("  (reusing the previous %s result, no new spend)" % model.split("/")[-1])
            row["models"][model] = r
            if r["cost"]:
                total_cost += r["cost"]
            label = model.split("/")[-1]
            print("")
            print("--- %s   %.2fs   $%s" % (label, r["latency"], ("%.5f" % r["cost"]) if r["cost"] is not None else "?"))
            print(r["text"] or ("ERROR: " + str(r["error"])))

        texts = [row["models"][m]["text"] for m in models]
        if len(models) == 2 and all(texts):
            diffs = disagreements(texts[0], texts[1])
            print("")
            print("--- %d disagreement(s)" % len(diffs))
            for d in diffs:
                print("    %s" % d)
            row["disagreements"] = diffs

        results.append(row)

    print("")
    print("=" * 78)
    print("audio    : %.1f s" % total_seconds)
    print("spend    : $%.5f" % total_cost)
    per_model = {}
    for row in results:
        for name, r in row["models"].items():
            if r.get("cost"):
                per_model[name] = per_model.get(name, 0.0) + r["cost"]
    if total_seconds:
        hours = total_seconds / 3600
        for name, spent in sorted(per_model.items()):
            print("  %-34s $%.5f   ->  $%.2f / hour of audio" % (name, spent, spent / hours))
    print("=" * 78)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print("written: %s" % args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
