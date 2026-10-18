# BOGOS-bot

A Discord bot for a Warhammer 40K group. Two jobs so far: it transcribes voice channels, and it reports who has filled their pairing sheet.

Fork of [Leehyunbin0131/Discord-Realtime-STT-Bot](https://github.com/Leehyunbin0131/Discord-Realtime-STT-Bot).

## Commands

| Command | What it does |
|---|---|
| `!transcript` | Joins your voice channel and starts listening |
| `!stop` | Finishes the queued speech and posts the transcript |
| `!pairing <sheets link>` | Reports who still owes matchups or a list, per team tab |

## How transcription works

Not real time, and not per utterance. Silero VAD cuts each speaker's speech, the
pieces accumulate in a per-speaker buffer, and the moment that buffer holds five
minutes it is sent to the model in one request. A whole chunk gives the model the
context that makes it spell the game's proper nouns the way the table did; a
sentence at a time it guesses, which is what "battle shop" was.

Chunks are kept per speaker, because concatenating two people into one stream
would read as dialogue but lose who said what, and a transcript without names is
not worth much in a Discord log.

Each chunk is written to the channel file as it lands, so a two-hour session is
settled as it runs rather than in one wait at the end. At `!stop` the audio
queue is drained, the buffer is flushed, and the tail is posted with the rest.

Per-speaker chunks come out in completion order, not in the order they were
spoken: the quiet player's last five minutes can be built before the talkative
one's first. Every chunk carries the time its first utterance arrived, so the
session is sorted back into that order before the file is written and the
channel is posted to.

The size is set by the provider, not by preference: a request body is capped at
20 MB and base64 inflates by a third, so 16 kHz mono tops out near 7.8 minutes.
Five minutes encodes to 12.8 MB.

```
audio -> VAD -> per-speaker buffer -> 5-minute chunk -> model -> channel
```

### Two ways the model drops content, and what stops them

A chunk can come back as its opening sentence. The model reports
`finish_reason: "stop"` after six completion tokens and the rest of the audio is
simply not written down. It is deterministic per input but not predictable from
the audio: the same 24 seconds of speech read back as 61 words unlevelled, 1 word
levelled, and 5 words levelled in two parts. Nothing downstream can tell a
six-word reading of five minutes from a quiet five minutes.

A chunk can also come back as a clock -- a 300-second chunk answered `00:00`
through `04:59`, one label per second, and no speech at all.

Both are caught in `stt/gemini_transcriber.py`, where the audio length is known:
a reading under 0.8 words per second, or one that is nothing but timestamps, is
re-asked once with a prompt that names the failure, and the longer of the two
answers is kept either way.

The model is `google/gemini-2.5-flash`, not the lite tier. Measured on two
recordings of one speaker, lite lost the content of four of six shapings of the
same audio, and flash lost none.

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

- Do not trust a clean-speech benchmark for this job. On real French Warhammer speech, models that score well on read speech replace the proper nouns with common French words. Ours was chosen on the table, not on the benchmark.
- The silence floor (`STT_MIN_SPEECH_DBFS`) is set by eye. The 77 recorded utterances run from -96 to -40 dBFS with no gap in between, so there is nothing to tune it against yet.
- `!pairing` reads the sheet over its public link export and sends no credentials. If the sheet is private, it will not work.
- Both the session recorder and the per-utterance dumps write other people's voices to disk. The widget's Record switch turns both off and picks the folder; that switch wins over `.env`. Ask people before you turn it on.
- `CLEANUP_ENABLED` is off. A text pass cannot tell a misheard word from a real one, so it has nothing useful to do.
- Only run on Windows so far, and only against French speech.

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Credits

Built on [Leehyunbin0131/Discord-Realtime-STT-Bot](https://github.com/Leehyunbin0131/Discord-Realtime-STT-Bot), which provides the process-isolated skeleton, the VAD pipeline and the resampler. This fork adds cloud STT, chunked transcription, batched output, the guard chain, the pairing reader and the recorder.

MIT. See [LICENSE](LICENSE).
