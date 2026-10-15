#!/bin/sh
# Runs as the box's initCommand, i.e. on create, on every resume, and after
# a snapshot restore. Idempotent on purpose: a second copy would give
# Discord two gateway sessions on the same token and one would be dropped.
set -e

APP="/workspace/discord-transcriber"
LOG="$APP/bot.log"

if pgrep -f "discord-transcriber.*main.py" >/dev/null 2>&1; then
  echo "bot already running"
  exit 0
fi

cd "$APP"
# nohup + & so the container start is not blocked by a foreground bot.
nohup ./.venv/bin/python main.py >>"$LOG" 2>&1 &
echo "bot started, pid $!"
