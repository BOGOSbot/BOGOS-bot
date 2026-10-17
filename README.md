# BOGOS-bot

A Discord bot for a Warhammer 40K group. Two jobs so far: it transcribes voice channels, and it reports who has filled their pairing sheet.

Fork of [Leehyunbin0131/Discord-Realtime-STT-Bot](https://github.com/Leehyunbin0131/Discord-Realtime-STT-Bot).

## Commands

| Command | What it does |
|---|---|
| `!transcript` | Joins your voice channel and starts listening |
| `!stop` | Reconciles the two readings and posts the transcript |
| `!pairing <sheets link>` | Reports who still owes matchups or a list, per team tab |

Transcription is not live. Silero VAD cuts the audio, a worker pool transcribes every utterance **twice** with two different models, and the two readings are reconciled at word level as they go. Nothing is posted during the session. At `!stop` an LLM settles the differences the mechanical pass could not, and the reconciled transcript is posted to the channel as a dated file, `transcription-2026-10-17-1831.md`. Both original readings are written to disk beside it.

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
- The reconciler never hears the audio, so it is never asked which reading is right. It settles what the text can settle and keeps both readings when it cannot, flagged in place. Expect roughly one difference in three to stay flagged.
- Reconciliation is not free in time. The free SenseNova endpoint answers in about five seconds per disputed line, so a quarter-hour session takes a few minutes to settle. If it fails, the flagged transcript is posted anyway: a rough transcript beats a missing one.
- Only run on Windows so far, and only against French speech.

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Credits

Built on [Leehyunbin0131/Discord-Realtime-STT-Bot](https://github.com/Leehyunbin0131/Discord-Realtime-STT-Bot), which provides the process-isolated skeleton, the VAD pipeline and the resampler. This fork adds cloud STT, batched output, the guard chain, the pairing reader and the recorder.

MIT. See [LICENSE](LICENSE).
