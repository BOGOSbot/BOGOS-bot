#!/bin/sh
# One-time dependency install for the transcription bot on an Upstash Box.
# discord.py bundles libopus only for Windows/macOS, so Linux needs it.
set -e
export DEBIAN_FRONTEND=noninteractive
APP="/workspace/home/bogos-bot"

sudo apt-get update -qq
sudo apt-get install -y -qq libopus0 ffmpeg
echo "APT_OK"

cd "$APP"
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
# CPU torch only: the VAD needs it, nothing needs CUDA here.
./.venv/bin/pip install -q torch --index-url https://download.pytorch.org/whl/cpu
./.venv/bin/pip install -q -r requirements.txt
echo "DEPS_OK"
