"""Nothing in the published tree may be a secret.

The keys live in .env, and .gitignore keeps .env out of the repository. These
tests are the check that says so, run on every pass instead of remembered at
push time. They are deliberately narrow: a real key shape, or a value that is
actually on this machine, and nothing else, so they do not cry wolf over the
model names and example paths that legitimately live in the tree.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SHAPES = {
    "an OpenRouter key": re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}"),
    "an OpenAI-shaped key": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}"),
    "a Discord bot token": re.compile(r"[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27}"),
    "a Google API key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "an AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "a GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "a Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "a Hugging Face token": re.compile(r"hf_[A-Za-z0-9]{30,}"),
    "a Stripe live key": re.compile(r"sk_live_[A-Za-z0-9]{20,}"),
    "a private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

# Text that means "this is a placeholder", not "this is a secret".
PLACEHOLDER = re.compile(r"replace_with|your_|changeme|example|xxxx", re.IGNORECASE)

# Only these names hold a secret; a model id or a path is not one.
SECRET_NAME = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD)")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def _tracked() -> list[Path]:
    result = _git("ls-files")
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return [ROOT / name for name in result.stdout.splitlines()]


def _text_of(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def test_the_env_file_is_never_tracked():
    result = _git("ls-files", "--error-unmatch", ".env")
    assert result.returncode != 0, ".env is tracked; every key in it is now public"


def test_the_env_file_is_ignored():
    assert _git("check-ignore", "-q", ".env").returncode == 0, ".gitignore lost .env"


def test_no_tracked_file_carries_key_shaped_text():
    found = []
    for path in _tracked():
        text = _text_of(path)
        for label, pattern in SHAPES.items():
            for match in pattern.finditer(text):
                if PLACEHOLDER.search(match.group(0)):
                    continue
                found.append(f"{path.relative_to(ROOT)} holds {label}")
    assert not found, "; ".join(found)


def test_the_published_example_ships_an_empty_key():
    line = next(
        (
            ln
            for ln in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
            if ln.startswith("OPENROUTER_API_KEY=")
        ),
        None,
    )
    assert line == "OPENROUTER_API_KEY=", f"the published example carries a key: {line!r}"


def test_no_key_that_is_on_this_machine_appears_in_a_tracked_file():
    """The strongest form: take the real keys and go looking for them."""
    env = ROOT / ".env"
    if not env.exists():
        pytest.skip("no .env on this machine")

    secrets = []
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if SECRET_NAME.search(name.upper()) and len(value) >= 12 and not PLACEHOLDER.search(value):
            secrets.append(value)
    if not secrets:
        pytest.skip("nothing secret in .env")

    found = [
        str(path.relative_to(ROOT))
        for path in _tracked()
        if any(value in _text_of(path) for value in secrets)
    ]
    assert not found, "a key from this machine is committed in: " + ", ".join(found)
