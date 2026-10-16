#!/bin/sh
# Runs as the box's initCommand: on create, on every resume, and after a
# snapshot restore. Idempotent on purpose - a second copy would give Discord
# two gateway sessions on one token and one would be dropped.
set -e

APP="/workspace/home/bogos-bot"
LOG="$APP/bot.log"
PIDFILE="$APP/bot.pid"

# A pgrep pattern cannot tell these apart cheaply (the command line is just
# .venv/bin/python main.py), so track the pid instead.
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "bot already running (pid $(cat "$PIDFILE"))"
  exit 0
fi

cd "$APP"
# nohup + & so the container start is not blocked by a foreground bot.
nohup ./.venv/bin/python main.py >>"$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "bot started, pid $!"
