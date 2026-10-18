# The always-on host

*An OVH VPS, or a mini-PC at home. Discord voice is outbound UDP with no TCP
fallback, which is what ruled out the Box route; an always-on host also makes
the entire wake layer unnecessary.*

The unit is `deploy/systemd/bogos-bot.service`. This is how the secrets get
onto the box, which is the part `.env` does not solve.

## Why not `.env` on the box

systemd's own man page, on `EnvironmentFile=`:

> Note that environment variables are not suitable for passing secrets (such as
> passwords, key material, ...) to service processes. Environment variables set
> for a unit are exposed to unprivileged clients via D-Bus IPC, and generally
> not understood as being data that requires protection. Moreover, environment
> variables are propagated down the process tree, including across security
> boundaries (such as setuid/setgid executables), and hence might leak to
> processes that should not have access to the secret data. Use
> `LoadCredential=`, `LoadCredentialEncrypted=` or `SetCredentialEncrypted=`
> (see below) to pass data to unit processes securely.

So a `.env` on the VPS is a plaintext key that `systemctl show bogos-bot` can
print, that anything running as the service user can read, and that rides along
in every backup and snapshot of the disk.

## Put the secrets on the box

`systemd-creds` and `LoadCredentialEncrypted=` arrived in **systemd 250**
(v250 release notes). Ubuntu 24.04 and Debian 12 are past it; **Ubuntu 22.04 is
not** — it ships 249, where you would use `LoadCredential=` against a
root-owned `600` file instead: the secret stays in plaintext on disk, but it is
still out of the environment and out of `systemctl show`. Check first:

`@sh
systemd --version | head -1
`@

Then type each secret once, into a prompt that does not echo, piped straight into
`systemd-creds`. The plaintext never reaches the disk and never reaches your
shell history.

`@sh
sudo mkdir -p /etc/credstore.encrypted

read -rs -p 'Discord token:  ' T && printf %s "$T" |
  sudo systemd-creds encrypt --name=discord-token - /etc/credstore.encrypted/discord-token

read -rs -p 'OpenRouter key: ' K && printf %s "$K" |
  sudo systemd-creds encrypt --name=openrouter - /etc/credstore.encrypted/openrouter
`@

`--name=` must match the credential name in the unit. It is bound into the
ciphertext, so a credential cannot be renamed or reused under another name.

`systemd-creds encrypt` binds the key to this machine: the host secret at
`/var/lib/systemd/credential.secret`, and the TPM2 if there is one. That is the
point — a copy of `/etc/credstore.encrypted/openrouter` is useless on any other
machine, which is what makes it safe in a snapshot, a backup, or a stolen disk.

Check it decrypts, then restart:

`@sh
sudo systemd-creds decrypt /etc/credstore.encrypted/openrouter - | head -c 12; echo
sudo systemctl restart bogos-bot
`@

To rotate: re-run the `encrypt` line with the new value, then restart. The unit
does not change.

The unit loads them by name — `LoadCredentialEncrypted=openrouter` with no path
resolves `/etc/credstore.encrypted/openrouter` — and systemd exposes each as a
file at `$CREDENTIALS_DIRECTORY/<name>`, readable by that service alone and only
while it runs. `config.py` reads them from there; on a workstation there is no
`CREDENTIALS_DIRECTORY` and it is the plain environment variable, which is how
`.env` and the widget reach it.

## Everything that is not a secret

`/etc/bogos-bot.env`, read by `EnvironmentFile=`:

`@env
STT_BACKEND=openrouter
STT_CHUNK_MODEL=google/gemini-2.5-flash
STT_DUMP_DIR=
IDLE_EXIT_MINUTES=0
RECORD_DIR=/var/lib/bogos-bot/recordings
TRANSCRIPT_SAVE_DIR=/var/lib/bogos-bot/transcripts
LOG_LEVEL=INFO
`@

`IDLE_EXIT_MINUTES=0` because an always-on host has nothing to save by exiting.
`STT_DUMP_DIR` must be empty: it writes other people's voices to disk.

## Install

`@sh
sudo useradd --system --home /opt/bogos-bot --shell /usr/sbin/nologin bogos
sudo git clone https://github.com/BOGOSbot/BOGOS-bot.git /opt/bogos-bot
sudo python3 -m venv /opt/bogos-bot/.venv
sudo /opt/bogos-bot/.venv/bin/pip install -r /opt/bogos-bot/requirements.txt
sudo apt-get install -y libopus0 ffmpeg
sudo install -m 644 /opt/bogos-bot/deploy/systemd/bogos-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bogos-bot
journalctl -u bogos-bot -f
`@

`libopus0` and `ffmpeg` are not optional. discord.py bundles libopus for Windows
and macOS only, and Discord voice needs it on Linux.

## What the widget does not cover

The widget is a Windows panel. On the VPS there is nothing to click, so
`recording.json` has no writer and the REC latch does not exist: set
`RECORD_DIR` and `STT_DUMP_DIR` in `/etc/bogos-bot.env` instead. That is what
they are the fallback for — see `recording.py`.

The same goes for the glossary: the key line and the pairing-sheet link are both
widget and `!transcript` features, and `!transcript <link>` works unchanged on
the VPS.

## Known gap

`ProtectSystem=strict` is not in the unit, because the bot writes its own state —
`status.json`, `live-session.json`, `glossary.session.txt`, `recording.json` —
into the checkout. Pinning those under `/var/lib/bogos-bot` would let the code
tree be read-only, which is the next step for hardening this unit.
