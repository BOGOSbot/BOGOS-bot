# Start the Discord transcription bot. Reads all settings from .env.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path .venv\Scripts\python.exe)) { throw 'No .venv found - create it first.' }
if (-not (Test-Path .env)) { throw 'No .env found - copy .env.example and set DISCORD_TOKEN.' }
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
& .venv\Scripts\python.exe main.py
