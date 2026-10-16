# Start the Discord transcription bot. Reads all settings from .env.
#
#   .\run.ps1                                  normal session
#   .\run.ps1 -RecordDir E:\corpus             also record the audio to disk
#
# -RecordDir overrides RECORD_DIR from .env for this run only, which is the
# point: recording captures other people's voices, so it should be a decision
# taken per session rather than a setting that stays on.
param(
    [string]$RecordDir = ''
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path .venv\Scripts\python.exe)) { throw 'No .venv found - create it first.' }
if (-not (Test-Path .env)) { throw 'No .env found - copy .env.example and set DISCORD_TOKEN.' }

# Two instances would hold the same Discord token and fight over the session,
# so the second launch is refused rather than left to flap.
$running = @(Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
  Where-Object { $_.CommandLine -like '*main.py*' })
if ($running.Count -gt 0) {
  Write-Host "Already running (PID $($running[0].ProcessId)) - only one instance can hold the Discord session." -ForegroundColor Yellow
  exit 1
}

if ($RecordDir) {
  # Set before python starts: python-dotenv does not override a variable that
  # is already in the environment, so this wins over .env.
  $env:RECORD_DIR = $RecordDir
  Write-Host "Recording the session's audio to $RecordDir" -ForegroundColor Yellow
}

$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
& .venv\Scripts\python.exe main.py
