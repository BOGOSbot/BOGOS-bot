"""Run the same audio through several STT models and report cost and agreement.

The point is not to crown a winner. It is to make the disagreements visible,
because that is where the meaning is: a model that quietly swaps a proper noun
for a common word costs exactly the same as one that gets it right, and a
benchmark on clean read speech cannot see the difference.

Two models that disagree are also two independent witnesses. This prints the
disagreements in a form a human can adjudicate by ear.

    python tools/compare_stt.py <wav> [<wav> ...] [--models a,b] [--jobs 6]
"""

from __future__ import annotations

import argparse
import base64
import difflib
import json
import re
import sys
import time
import urllib.error
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import api_cfg  # noqa: E402  -- loads .env and the key

ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_MODELS = ["openai/gpt-transcribe", "mistralai/voxtral-small-24b-2507-stt"]
RETRYABLE = ("429", "500", "502", "503", "504")


def _once(path: Path, model: str, language: str) -> dict:
    """One request. Text, latency, and what the response says it cost."""
    payload = {
        "model": model,
        "input_audio": {
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            "format": "wav",
        },
        "usage": {"include": True},
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
        detail = exc.read().decode("utf-8", "replace")[:180]
        return {"text": "", "error": "HTTP %s %s" % (exc.code, detail), "latency": 0.0, "cost": None}
    except Exception as exc:
        return {"text": "", "error": str(exc)[:180], "latency": 0.0, "cost": None}
    elapsed = time.perf_counter() - started

    usage = data.get("usage") or {}
    return {
        "text": (data.get("text") or "").strip(),
        "error": None,
        "latency": elapsed,
        "cost": usage.get("cost"),
    }


def transcribe(path: Path, model: str, language: str) -> dict:
    """With a backoff: a burst of hundreds of requests will hit a rate limit."""
    last = _once(path, model, language)
    for attempt in range(3):
        if not last.get("error") or not any(c in last["error"] for c in RETRYABLE):
            return last
        time.sleep(1.5 * (attempt + 1))
        last = _once(path, model, language)
    return last


def words(text: str) -> list:
    """A comparison unit: words without punctuation, lowercased."""
    return re.findall(r"[\w\u00c0-\u017f]+", text.lower())


def disagreements(a: str, b: str) -> list:
    """The phrases where two transcripts part company."""
    left, right = words(a), words(b)
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        out.append(
            "%s -> %s"
            % (" ".join(left[i1:i2]) or "(nothing)", " ".join(right[j1:j2]) or "(nothing)")
        )
    return out


def duration_of(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wavs", nargs="*")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--language", default="fr")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--reuse", default="")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--dump-dir", default="")
    parser.add_argument("--show", type=int, default=12)
    args = parser.parse_args()

    if args.dump_dir:
        wavs = sorted(Path(args.dump_dir).glob("*.wav"))
    else:
        wavs = [Path(w) for w in args.wavs]
    wavs = [w for w in wavs if w.is_file()]
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    reuse = {}
    if args.reuse and Path(args.reuse).is_file():
        for row in json.loads(Path(args.reuse).read_text(encoding="utf-8")):
            for name, r in row.get("models", {}).items():
                reuse[(row["file"], name)] = r

    results = {}
    tasks = [(p, m) for p in wavs for m in models if (p.name, m) not in reuse]
    print("files %d   models %d   calls %d   jobs %d" % (len(wavs), len(models), len(tasks), args.jobs))

    started = time.perf_counter()
    if tasks:
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            futures = {pool.submit(transcribe, p, m, args.language): (p, m) for p, m in tasks}
            done = 0
            for future in as_completed(futures):
                p, m = futures[future]
                try:
                    results[(p.name, m)] = future.result()
                except Exception as exc:
                    results[(p.name, m)] = {"text": "", "error": str(exc)[:180], "latency": 0.0, "cost": None}
                done += 1
                if done % 50 == 0 or done == len(tasks):
                    print("  %d/%d   %.0fs" % (done, len(tasks), time.perf_counter() - started))
    for key, value in reuse.items():
        results.setdefault(key, value)
    print("wall clock: %.0fs" % (time.perf_counter() - started))

    rows = []
    for path in wavs:
        row = {"file": path.name, "duration": duration_of(path), "models": {}}
        for m in models:
            row["models"][m] = results.get(
                (path.name, m), {"text": "", "error": "not run", "latency": 0.0, "cost": None}
            )
        texts = [row["models"][m]["text"] for m in models]
        if len(models) == 2 and all(texts):
            row["disagreements"] = disagreements(texts[0], texts[1])
            row["identical"] = texts[0].strip() == texts[1].strip()
        rows.append(row)

    total_seconds = sum(r["duration"] for r in rows)
    per_model = {}
    failures = 0
    flagged = 0
    identical = 0
    for r in rows:
        for name, res in r["models"].items():
            if res.get("cost"):
                per_model[name] = per_model.get(name, 0.0) + res["cost"]
            if res.get("error"):
                failures += 1
        if r.get("identical"):
            identical += 1
        elif r.get("disagreements"):
            flagged += 1

    print("")
    print("=" * 74)
    print("audio        : %.1f s  (%.1f min)" % (total_seconds, total_seconds / 60))
    print("utterances   : %d" % len(rows))
    print("failures     : %d" % failures)
    print("identical    : %d" % identical)
    print("flagged      : %d  (%.0f%%)" % (flagged, 100.0 * flagged / max(len(rows), 1)))
    if total_seconds:
        hours = total_seconds / 3600
        for name, spent in sorted(per_model.items()):
            print("  %-38s $%.4f   ->  $%.2f / hour" % (name, spent, spent / hours))
        print("combined     : $%.4f" % sum(per_model.values()))
    print("=" * 74)

    if not args.quiet:
        for r in rows[: args.show]:
            print("")
            print("%s   %.1fs" % (r["file"], r["duration"]))
            for m in models:
                print("  %-16s %s" % (m.split("/")[-1][:16], r["models"][m]["text"][:110]))
            if r.get("disagreements"):
                print("  %d span(s)" % len(r["disagreements"]))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        print("written: %s" % args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
