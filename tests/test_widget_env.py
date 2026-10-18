"""The widget's one write into .env: it must not eat the rest of the file.

.env is hand-edited. The widget only ever sets OPENROUTER_API_KEY, and the
whole point of these tests is that comments, blank lines, ordering and the
line endings come back untouched. The widget is PowerShell, so the functions
are lifted out of it and driven here; the test skips where there is no
PowerShell to run them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WIDGET = Path(__file__).resolve().parents[1] / "widget" / "BOGOS-widget.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("powershell.exe") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    POWERSHELL is None or not WIDGET.exists(),
    reason="the widget is PowerShell; it needs powershell.exe to run",
)

EXAMPLE = """\
# Required
DISCORD_TOKEN=replace_with_your_discord_bot_token

# Backend: local (faster-whisper) or openrouter (cloud STT endpoint).
STT_BACKEND=openrouter
OPENROUTER_API_KEY=

# Logging
LOG_LEVEL=INFO
"""

DRIVER = r"""
param([string]$Root, [string]$Widget, [string]$OpsJson)
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    (Get-Content -Raw $Widget), [ref]$null, [ref]$null)
$wanted = @('Read-EnvText', 'Get-EnvValue', 'Format-EnvValue', 'Set-EnvValue')
foreach ($f in $ast.FindAll({
        param($n)
        $n -is [System.Management.Automation.Language.FunctionDefinitionAst]
    }, $true)) {
    if ($wanted -contains $f.Name) { . ([scriptblock]::Create($f.Extent.Text)) }
}
$envFile = Join-Path $Root '.env'
foreach ($op in ($OpsJson | ConvertFrom-Json)) {
    if ($op.kind -eq 'set') { Set-EnvValue $op.name $op.value | Out-Null }
    elseif ($op.kind -eq 'get') { Write-Output ('VALUE=' + (Get-EnvValue $op.name)) }
}
"""


def _drive(tmp_path: Path, *ops: dict) -> str:
    driver = tmp_path / "driver.ps1"
    driver.write_text(DRIVER, encoding="utf-8")
    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(driver),
            "-Root", str(tmp_path),
            "-Widget", str(WIDGET),
            "-OpsJson", json.dumps(list(ops)),
        ],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _seed(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(EXAMPLE, encoding="utf-8")


def test_a_save_leaves_every_other_line_alone(tmp_path):
    _seed(tmp_path)
    (tmp_path / ".env").write_text(EXAMPLE, encoding="utf-8")

    _drive(tmp_path, {"kind": "set", "name": "OPENROUTER_API_KEY", "value": "sk-or-v1-abc123"})

    before = EXAMPLE.splitlines()
    after = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert len(before) == len(after)
    assert after == [
        "OPENROUTER_API_KEY=sk-or-v1-abc123" if line.startswith("OPENROUTER_API_KEY=") else line
        for line in before
    ]


def test_a_missing_env_is_seeded_from_the_example(tmp_path):
    _seed(tmp_path)

    _drive(tmp_path, {"kind": "set", "name": "OPENROUTER_API_KEY", "value": "sk-or-v1-abc123"})

    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DISCORD_TOKEN=replace_with_your_discord_bot_token" in text
    assert "OPENROUTER_API_KEY=sk-or-v1-abc123" in text


def test_a_key_absent_from_the_example_is_appended(tmp_path):
    (tmp_path / ".env").write_text("DISCORD_TOKEN=x\n", encoding="utf-8")

    _drive(tmp_path, {"kind": "set", "name": "OPENROUTER_API_KEY", "value": "sk-or-v1-abc123"})

    lines = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "DISCORD_TOKEN=x"
    assert lines[-1] == "OPENROUTER_API_KEY=sk-or-v1-abc123"


def test_saving_twice_does_not_grow_the_file(tmp_path):
    _seed(tmp_path)
    (tmp_path / ".env").write_text(EXAMPLE, encoding="utf-8")

    for value in ("sk-or-v1-first", "sk-or-v1-second"):
        _drive(tmp_path, {"kind": "set", "name": "OPENROUTER_API_KEY", "value": value})

    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert text.count("OPENROUTER_API_KEY=") == 1
    assert "sk-or-v1-second" in text and "sk-or-v1-first" not in text
    assert text.count("\n") == EXAMPLE.count("\n")


def test_the_key_comes_back_out(tmp_path):
    _seed(tmp_path)

    out = _drive(
        tmp_path,
        {"kind": "set", "name": "OPENROUTER_API_KEY", "value": "sk-or-v1-abc123"},
        {"kind": "get", "name": "OPENROUTER_API_KEY"},
    )

    assert "VALUE=sk-or-v1-abc123" in out


def test_a_value_with_a_space_is_quoted_so_dotenv_reads_it_whole(tmp_path):
    _seed(tmp_path)

    _drive(tmp_path, {"kind": "set", "name": "OPENROUTER_API_KEY", "value": "two words"})

    assert 'OPENROUTER_API_KEY="two words"' in (tmp_path / ".env").read_text(encoding="utf-8")


def test_clearing_empties_the_value_without_removing_the_key(tmp_path):
    _seed(tmp_path)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-abc123\n", encoding="utf-8")

    out = _drive(
        tmp_path,
        {"kind": "set", "name": "OPENROUTER_API_KEY", "value": ""},
        {"kind": "get", "name": "OPENROUTER_API_KEY"},
    )

    assert "VALUE=" in out and "VALUE=sk-or" not in out
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "OPENROUTER_API_KEY=\n"


def test_the_file_keeps_its_line_endings_and_gains_no_bom(tmp_path):
    _seed(tmp_path)
    (tmp_path / ".env").write_bytes(EXAMPLE.replace("\n", "\r\n").encode("utf-8"))

    _drive(tmp_path, {"kind": "set", "name": "OPENROUTER_API_KEY", "value": "sk-or-v1-abc123"})

    raw = (tmp_path / ".env").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.count(b"\r\n") == EXAMPLE.count("\n")
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_an_unreadable_value_is_reported_not_guessed(tmp_path):
    """A quoted value in the file is handed back without its quotes."""
    _seed(tmp_path)
    (tmp_path / ".env").write_text('OPENROUTER_API_KEY="sk-or-v1-abc123"\n', encoding="utf-8")

    out = _drive(tmp_path, {"kind": "get", "name": "OPENROUTER_API_KEY"})

    assert "VALUE=sk-or-v1-abc123" in out
