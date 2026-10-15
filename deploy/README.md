# Wake-on-demand deployment

*The bot is used a few hours a week, unpredictably. Paying for an always-on box
would be wasted: instead the Box sleeps, and a free Worker wakes it from Discord.*

## Shape

```
someone types /wake in Discord
        |
        v
Cloudflare Worker  (always on, free)          <- verifies Discord's signature
        |  POST /v2/box/{id}/resume
        v
Upstash Box  (paused, $0.10/active CPU hour)  <- resumes
        |  initCommand runs on every resume
        v
start-bot.sh -> the bot connects, ready for !transcript
```

Why a Worker at all: a paused box cannot hear Discord, so a sleeping bot can
never react to `!transcript`. Something always-on must observe the trigger.
This is that always-on something, and it costs nothing.

## Costs

- Cloudflare Worker: free tier (100k requests/day, this uses a handful).
- Upstash Box: pay-as-you-go, $0.10 per active CPU hour, auto-pauses when idle.
  A few hours a week is roughly $1-2/month. Do NOT use keepAlive ($8/month):
  keep-alive boxes cannot pause or resume at all.

## What only you can do

1. **Create a free Cloudflare account** — no automation can do this for you.
   Then `npx wrangler login` on this machine.
2. **Approve creating the Upstash Box** and uploading the bot's `.env` to it
   (Discord token + OpenRouter key leave this machine).
3. **Approve the Discord app change**: set the Interactions Endpoint URL and
   register the `/wake` command.

## Steps

### 1. Create the box

```powershell
box create --json --no-use --runtime python --size small --name transcriber
```

Keep it a plain box: no `--keep-alive`. Set the init command to start the bot
on every resume:

```
initCommand: sh /workspace/discord-transcriber/deploy/box/start-bot.sh
```

### 2. Dependencies

discord.py bundles libopus only for Windows and macOS, so Linux needs it.

```sh
apt-get update && apt-get install -y libopus0 ffmpeg
cd /workspace/discord-transcriber
python3 -m venv .venv
./.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
./.venv/bin/pip install -r requirements.txt
```

### 3. Get the code and secrets in

Two options: push this fork to a private GitHub repo and clone it on the box
(the box already carries a GitHub credential), or stream a tarball in base64
per `notes/upstash-box.md`. The repo route makes updates a `git pull`.

Write `.env` on the box with:

```env
DISCORD_TOKEN=...
OPENROUTER_API_KEY=...
STT_BACKEND=openrouter
STT_API_MODEL=mistralai/voxtral-small-24b-2507-stt
CLEANUP_ENABLED=false
STT_DUMP_DIR=
```

`STT_DUMP_DIR` must be empty on the box: it otherwise writes other people's
voice to Upstash's disk.

### 4. Deploy the Worker

```powershell
cd deploy\cloudflare-worker
npx wrangler deploy
npx wrangler secret put DISCORD_PUBLIC_KEY
npx wrangler secret put UPSTASH_BOX_API_KEY
npx wrangler secret put BOX_ID
```

### 5. Point Discord at it

Register the slash command (bot token, app id from the Developer Portal):

```powershell
curl -X PUT "https://discord.com/api/v10/applications/<APP_ID>/commands" `
  -H "Authorization: Bot <TOKEN>" -H "Content-Type: application/json" `
  -d '{"name":"wake","description":"Start the transcription bot (about a minute)","type":1}'
```

Then set the app's Interactions Endpoint URL to the Worker's URL. Discord
immediately sends a PING; the Worker answers it, which is what proves the
endpoint. Doing this through the Developer Portal UI is fine.

## Known unknown

**Measured: 23 s** from resume to the bot process running, so `/wake` then
`!transcript` is comfortable but not instant.

`/wake` is gone, so prefix commands are the whole interface again and the
`applications.commands` scope is not needed.
## Measured on the real box (2026-10-15)

These cost time to discover; do not re-derive them.

- **`/workspace` is root-owned.** Creating anything directly under it fails
  with `Permission denied`. `/workspace/home` is the box user's cwd and is
  writable, so the app lives at `/workspace/home/discord-transcriber`.
- **`sudo` works headlessly** for `apt-get` (the box user is a sudoer).
- **The box is arm64**, and the PyTorch CPU index serves aarch64 wheels, so
  `torch --index-url https://download.pytorch.org/whl/cpu` installs fine.
  Python on the box is 3.13.
- **`files upload` handles a 33 KB tarball** — the chunked-base64 workaround
  is only needed above ~1 MiB.
- **Discord's API 403s an empty body for PowerShell's default User-Agent.**
  `Invoke-RestMethod` against `/applications/...` fails that way while
  `/users/@me/guilds` succeeds, which makes it look like a permission bug.
  Send an explicit `User-Agent` (curl does what you expect).
- **The invite must include the `applications.commands` scope**, or slash
  commands exist but cannot be used in the guild:
  `https://discord.com/oauth2/authorize?client_id=<APP_ID>&permissions=1051648&scope=bot%20applications.commands`

## Outcome: torn down (2026-10-16)

The pattern works. It is not usable for this bot.

Every piece was verified working: the Worker woke the box, the box resumed,
`initCommand` restored the bot, and a paused box had the bot running **23
seconds** later. Then the bot joined a voice channel and heard nothing.

**A Box cannot send UDP beyond DNS**, even with `network_policy` already set
to `allow-all`. Discord voice media is UDP/SRTP with no TCP fallback, so the
websocket handshake completes (`Voice handshake complete. Endpoint found:
c-ewr17-...`) and the connection then times out. No configuration fixes it.

The box, the Worker, the `/wake` command and the Interactions Endpoint URL
were all deleted. What remains in this folder is the pattern and the measured
traps, kept because they are reusable for any workload that does not need UDP
and because re-deriving them is expensive.

A voice bot needs a host that allows outbound UDP: this PC does, and so would
a small EU VPS or a home mini-PC. On an always-on host the entire wake layer
is unnecessary — set `IDLE_EXIT_MINUTES=0` and run the bot directly.
- **A body-less POST does not resume a box.** `fetch` with no body sends no
  `Content-Length`, and Upstash answers 4xx; PowerShell's `Invoke-RestMethod`
  always sends an empty body, so the same call succeeds there and hides the
  bug. Send `body: "{}"`. This is what made `/wake` reply correctly while
  the box stayed asleep: the interaction was fine, the resume was rejected.
- **Diagnose a stuck wake with `GET /?diag=<DIAG_TOKEN>`.** It runs the real
  resume and returns the upstream status and body, so the failure is visible
  instead of being swallowed by `ctx.waitUntil`.