# discord-transcriber

*Transcribes a French voice channel in scheduled batches, and reports who has filled their pairing sheet. Built for a Warhammer 40K group, kept general.*

Fork of [Leehyunbin0131/Discord-Realtime-STT-Bot](https://github.com/Leehyunbin0131/Discord-Realtime-STT-Bot) (MIT), substantially reworked. See **Credits** at the bottom.

---

## What it does

| Command | Effect |
|---|---|
| `!transcript` | Joins your voice channel and posts the transcript back as it goes |
| `!stop` | Finishes what is queued, posts it, and leaves |
| `!pairing <google sheets link>` | Reports, per team tab of a tournament workbook, who still owes matchups or an army list |

Not real time, deliberately. Audio is segmented by Silero VAD, queued, and transcribed by a worker pool; an ordered emitter re-sequences the results and posts them in batches. A slow model delays the transcript but never blocks the audio path or drops speech.

---

## Quick start

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
cp .env.example .env                            # then set DISCORD_TOKEN
```

On Windows there is a launcher: `./run.ps1`, and `./run.ps1 -RecordDir E:\recordings` to also capture the session audio for corpus building.

The invite needs **both** the `bot` and `applications.commands` scopes, plus the **Message Content** intent enabled in the Developer Portal — prefix commands read message text, and without it the bot simply never hears you.

---

## Configuration

Everything lives in `.env`; `.env.example` documents the lot. The ones that matter most:

| Key | Why |
|---|---|
| `STT_BACKEND` | `openrouter` (cloud, current) or `local` (faster-whisper) |
| `STT_API_MODEL` | `openai/gpt-transcribe` — see the model note below |
| `VAD_MIN_SILENCE_MS` | **600**, not Silero's 100 default: 100 ms cuts at every inter-word pause and shreds the transcript into fragments |
| `IDLE_EXIT_MINUTES` | Exit after this long idle, so an auto-pausing host can sleep. `0` on a PC or an always-on VPS |
| `RECORD_DIR` | Record every speaker's audio for corpus building. Off by default — it captures other people's voices |

---

## What the measurements taught us

This is the part worth reading, because most of it contradicts what the obvious choices would have been.

**Pick the model on your own audio, not on a benchmark.** Forty clean FLEURS utterances ranked `voxtral-small-24b` first at 1.4% WER against `gpt-transcribe` at 2.9%. On real domain speech it inverted: voxtral wrote *Louvre* for **Drukhari** and *écrans* for **Nécrons** — swapping the proper nouns that carry the meaning for common French words that fit its language model — while gpt-transcribe recovered both, and ran 3× faster. FLEURS contains no proper nouns, so it could not see the failure that mattered. The benchmark measured the wrong thing.

**Segmentation beats model choice.** VAD defaults of 100 ms silence cut at every inter-word pause: 13 fragments over 28.6 s. At 600 ms the same audio gave 4 sentences over 13.7 s, word-perfect. Token cost unchanged.

**Longer context hurts.** On the same audio, one long request scored 2.8% WER, twenty short ones 1.9% — at identical cost.

**Confidence cannot detect hallucination.** The worst hallucination in the set — *"Sous-titrage ST 501"* on 1.9 s of near silence — scored the model's **highest** log probability, with individual words at p=0.997 and `no_speech_prob` 0.000. It is confidently wrong. What catches these is a **list of memorised subtitle credits**, because the phrase was never heard in the first place.

### The guards, and why each exists

| Guard | Trigger |
|---|---|
| 0 | Segment under 0.5 s — with VAD padding that is under 100 ms of anything, a click or a breath |
| 1 | Below a −60 dBFS floor: models answer silence with fluent invention |
| 2 | Loudness normalisation, so quiet speakers are not penalised |
| 3 | Words per second — fabrications recite training data at absurd rates |
| 4 | Memorised artifact list (`stt/artifacts.py`), validated on a real recording |

Guard 1 is deliberately **untuned**. The 77 recorded utterances run from −96.6 to −40 dBFS in a smooth continuum with no gap to put a boundary in, so no threshold can be justified without labelled data. Tightening it would be a guess wearing a number.

---

## Project structure

```
audio/      resampling (streaming windowed-sinc, one instance per speaker)
bot/        Discord client, audio sink, recorder, pairing reader
stt/        VAD, worker pool, transcriber backends, guards
tests/      pytest — the guards, the parser and the recorder
deploy/     notes for a Linux host, including what did not work
```

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Credits

Built on [Leehyunbin0131/Discord-Realtime-STT-Bot](https://github.com/Leehyunbin0131/Discord-Realtime-STT-Bot), which is MIT-licensed and provides the process-isolated skeleton, the VAD pipeline and the original resampler. This fork adds cloud STT, batched output, the guard chain, the pairing reader and the session recorder.

MIT — see [LICENSE](LICENSE).
