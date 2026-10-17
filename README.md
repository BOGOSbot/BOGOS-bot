# BOGOS-bot

A Discord bot for a Warhammer 40K group. Two jobs so far: it transcribes voice channels, and it reports who has filled their pairing sheet.

Fork of [Leehyunbin0131/Discord-Realtime-STT-Bot](https://github.com/Leehyunbin0131/Discord-Realtime-STT-Bot).

## Commands

| Command | What it does |
|---|---|
| `!transcript` | Joins your voice channel and posts the transcript as it goes |
| `!stop` | Finishes the queue, posts it, leaves |
| `!pairing <sheets link>` | Reports who still owes matchups or a list, per team tab |

Transcription is not live. Silero VAD cuts the audio, a worker pool transcribes it, and an ordered emitter posts batches of results. A slow model delays the transcript but never blocks the audio or drops speech.

## Install

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Set `DISCORD_TOKEN` in `.env`. Turn on the Message Content intent in the Discord Developer Portal, and invite the bot with both the `bot` and `applications.commands` scopes. Without Message Content it never sees the commands.

## Run

```bash
.venv/bin/python main.py
```

On Windows use `./run.ps1`. Add `-RecordDir E:\recordings` to also save the session audio. `widget/BOGOS-widget.ps1` is a small panel with a start/stop toggle. Minimising leaves it in the tray. It can only show a log for bots it started itself.

## Caveats

- `STT_API_MODEL` defaults to `openai/gpt-transcribe`. We chose it on real French Warhammer speech, where it read the proper nouns that cheaper models replaced with common French words. A clean-speech benchmark had ranked it second, because that benchmark has no proper nouns in it. Do not trust it.
- The silence floor (`STT_MIN_SPEECH_DBFS`) is set by eye. The 77 recorded utterances run from -96 to -40 dBFS with no gap in between, so there is nothing to tune it against yet.
- `!pairing` reads the sheet over its public link export and sends no credentials. If the sheet is private, it will not work.
- Both the session recorder and the per-utterance dumps write other people's voices to disk. The widget's Record switch turns both off and picks the folder; that switch wins over `.env`. Ask people before you turn it on.
- `CLEANUP_ENABLED` is off. A text pass cannot tell a misheard word from a real one, so it has nothing useful to do.
- The second opinion (`STT_SECOND_OPINION`) runs every utterance through a second model and prints its reading when the two disagree on meaning. Differences confined to grammar and filler are dropped, and the second reading faces the same rate and artifact guards as the first. It adds spend, not certainty: $0.18/hour on top of gpt's $0.27.
- Only run on Windows so far, and only against French speech.

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Credits

Built on [Leehyunbin0131/Discord-Realtime-STT-Bot](https://github.com/Leehyunbin0131/Discord-Realtime-STT-Bot), which provides the process-isolated skeleton, the VAD pipeline and the resampler. This fork adds cloud STT, batched output, the guard chain, the pairing reader and the recorder.

MIT. See [LICENSE](LICENSE).
