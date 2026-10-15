"""
Configuration module for Discord Realtime STT Bot.
Groups settings into logical dataclasses with environment variable loading and validation.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc


# ──────────────────────────────────────────────
# Discord
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class DiscordConfig:
    token: str = field(default_factory=lambda: _env_str("DISCORD_TOKEN", ""))
    command_prefix: str = field(default_factory=lambda: _env_str("COMMAND_PREFIX", "!"))


# ──────────────────────────────────────────────
# Audio Pipeline
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class AudioConfig:
    """Audio format constants for the Discord → STT pipeline."""
    input_sample_rate: int = 48_000   # Discord Opus decoded PCM
    input_channels: int = 2           # Stereo
    output_sample_rate: int = 16_000  # Whisper expected rate
    output_channels: int = 1          # Mono
    sample_width: int = 2             # int16 = 2 bytes per sample


# ──────────────────────────────────────────────
# VAD  (Silero)
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class VADConfig:
    repo_or_dir: str = "snakers4/silero-vad"
    model_name: str = "silero_vad"
    # Silero requires 512 samples @ 16 kHz = 32 ms
    frame_samples: int = 512
    frame_duration_ms: int = 32
    ring_buffer_frames: int = 10  # ~320 ms pre-speech context

    # End-of-speech tuning. Silero's default min_silence (100 ms) ends an
    # utterance at the first natural inter-word pause: the transcript gets
    # shredded into half-second fragments that Whisper mostly hallucinates on,
    # and every fragment costs a full encoder pass. 600 ms is a sentence pause.
    threshold: float = field(default_factory=lambda: _env_float("VAD_THRESHOLD", 0.5))
    min_silence_ms: int = field(
        default_factory=lambda: _env_int("VAD_MIN_SILENCE_MS", 600)
    )
    speech_pad_ms: int = field(
        default_factory=lambda: _env_int("VAD_SPEECH_PAD_MS", 200)
    )
    # Emit mid-speech past this length so a monologue streams out in chunks
    # instead of arriving as one huge blob after the speaker finally stops.
    max_utterance_seconds: float = field(
        default_factory=lambda: _env_float("VAD_MAX_UTTERANCE_SECONDS", 25.0)
    )

    @property
    def frame_bytes(self) -> int:
        """Bytes per VAD frame (512 samples × 2 bytes/sample)."""
        return self.frame_samples * 2


# ──────────────────────────────────────────────
# STT  (Faster-Whisper)
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class STTConfig:
    # Default tuned for CPU-only use on a French voice channel. Upstream
    # shipped large-v3-turbo, which needs a GPU. Measured French WER here:
    # base 24.6%, small 10.2% -- and small still runs at 1.2x realtime.
    model_id: str = field(
        default_factory=lambda: _env_str(
            "STT_MODEL_ID",
            "small",
        )
    )
    device: str = field(default_factory=lambda: _env_str("STT_DEVICE", "cuda"))
    compute_type: str = field(
        default_factory=lambda: _env_str("STT_COMPUTE_TYPE", "float16")
    )
    language: str = field(default_factory=lambda: _env_str("STT_LANGUAGE", "fr"))
    beam_size: int = field(default_factory=lambda: _env_int("STT_BEAM_SIZE", 1))
    min_audio_bytes: int = field(
        default_factory=lambda: _env_int("STT_MIN_AUDIO_BYTES", 8000)
    )  # Ignore audio < 0.25 s (4000 samples × 2 bytes): clicks, not speech

    # --- anti-hallucination guards -------------------------------------
    # ASR models do not return an empty string for silence, they invent
    # fluent text. Measured on real dumps: a -77 dBFS clip produced a full
    # fabricated sentence. These three guards are layered because no single
    # one is sufficient.
    #
    # 1. Floor: drop utterances this quiet before spending a request on them.
    min_speech_dbfs: float = field(
        default_factory=lambda: _env_float("STT_MIN_SPEECH_DBFS", -60.0)
    )
    # 2. Level: lift quiet speakers toward a consistent target. Real speech in
    #    the dumps ranged from -52 to -15 dBFS, which is a lot of lost signal.
    normalize_dbfs: float = field(
        default_factory=lambda: _env_float("STT_NORMALIZE_DBFS", -20.0)
    )
    max_gain_db: float = field(
        default_factory=lambda: _env_float("STT_MAX_GAIN_DB", 24.0)
    )
    # 3. Plausibility: nobody speaks this fast. A 0.6 s clip yielding a
    #    sixteen-word sentence is invented, whatever its level.
    # Tuned on real dumps: genuine fast technical French measured 5.9 words/s,
    # while fabrications started at 8.6 and ran to 84. The grace keeps short
    # but real replies ("Merci.", "Je ne sais pas") from being discarded.
    max_words_per_second: float = field(
        default_factory=lambda: _env_float("STT_MAX_WORDS_PER_SECOND", 7.0)
    )
    min_words_grace: int = field(
        default_factory=lambda: _env_int("STT_MIN_WORDS_GRACE", 5)
    )
    fallback_model_id: str = field(
        default_factory=lambda: _env_str("STT_FALLBACK_MODEL_ID", "base")
    )
    fallback_device: str = field(
        default_factory=lambda: _env_str("STT_FALLBACK_DEVICE", "cpu")
    )
    fallback_compute_type: str = field(
        default_factory=lambda: _env_str("STT_FALLBACK_COMPUTE_TYPE", "int8")
    )


# ──────────────────────────────────────────────
# Process Management
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class ProcessConfig:
    user_timeout_seconds: int = field(
        default_factory=lambda: _env_int("USER_TIMEOUT_SECONDS", 60)
    )
    speech_flush_timeout_seconds: float = field(
        default_factory=lambda: _env_float("SPEECH_FLUSH_TIMEOUT_SECONDS", 2.0)
    )
    result_poll_interval: float = field(
        default_factory=lambda: _env_float("RESULT_POLL_INTERVAL", 0.05)
    )  # 50 ms async poll
    audio_queue_maxsize: int = field(
        default_factory=lambda: _env_int("AUDIO_QUEUE_MAXSIZE", 512)
    )
    result_queue_maxsize: int = field(
        default_factory=lambda: _env_int("RESULT_QUEUE_MAXSIZE", 128)
    )
    command_queue_maxsize: int = field(
        default_factory=lambda: _env_int("COMMAND_QUEUE_MAXSIZE", 128)
    )
    # Parallel transcription workers. Cloud STT is network-bound, so a handful
    # of concurrent requests drains a backlog several times faster.
    transcribe_workers: int = field(
        default_factory=lambda: _env_int("STT_WORKERS", 4)
    )
    # Exit once nothing has happened for this long, so a woken box can fall
    # back asleep instead of billing active CPU hours overnight. 0 disables it.
    # On a host that should stay up (a PC, a VPS) this must be set to 0.
    idle_exit_minutes: int = field(
        default_factory=lambda: _env_int("IDLE_EXIT_MINUTES", 15)
    )
    stt_health_check_interval: float = field(
        default_factory=lambda: _env_float("STT_HEALTH_CHECK_INTERVAL", 2.0)
    )
    shutdown_timeout_seconds: float = field(
        default_factory=lambda: _env_float("SHUTDOWN_TIMEOUT_SECONDS", 10.0)
    )


# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class LogConfig:
    level: str = field(default_factory=lambda: _env_str("LOG_LEVEL", "INFO"))
    format: str = "%(asctime)s │ %(name)-18s │ %(levelname)-7s │ %(message)s"
    date_format: str = "%H:%M:%S"


# ──────────────────────────────────────────────
# Cloud STT (OpenRouter /audio/transcriptions)
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class APIConfig:
    """Cloud transcription backend, used when the local CPU is not enough.

    Prices on OpenRouter's STT endpoint are per second of audio, so even the
    expensive models cost well under $1 per hour of speech.
    """
    backend: str = field(
        default_factory=lambda: _env_str("STT_BACKEND", "local")
    )  # "local" (faster-whisper) or "openrouter"
    openrouter_key: str = field(
        default_factory=lambda: _env_str("OPENROUTER_API_KEY", "")
    )
    model: str = field(
        default_factory=lambda: _env_str(
            "STT_API_MODEL", "mistralai/voxtral-small-24b-2507-stt"
        )
    )
    timeout_seconds: float = field(
        default_factory=lambda: _env_float("STT_API_TIMEOUT", 120.0)
    )
    # Write every utterance here as a WAV (empty = off). For diagnosis: it is
    # exactly the audio the transcriber receives.
    dump_dir: str = field(default_factory=lambda: _env_str("STT_DUMP_DIR", ""))


# ──────────────────────────────────────────────
# Post-transcription cleanup (LLM)
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class CleanupConfig:
    """One LLM pass over a finished transcript, to fix what context can fix.

    Output is rendered as a diff rather than applied silently, so a wrong
    correction is visible and rejectable rather than quietly rewriting speech.
    """
    # Off by default: it runs after the ASR, so while a hallucination problem
    # is being diagnosed it only adds a second suspect.
    enabled: bool = field(default_factory=lambda: _env_bool("CLEANUP_ENABLED", False))
    openrouter_key: str = field(
        default_factory=lambda: _env_str("OPENROUTER_API_KEY", "")
    )
    model: str = field(
        default_factory=lambda: _env_str(
            "CLEANUP_MODEL", "deepseek/deepseek-v4.1-flash"
        )
    )
    timeout_seconds: float = field(
        default_factory=lambda: _env_float("CLEANUP_TIMEOUT", 180.0)
    )


# ──────────────────────────────────────────────
# Transcript output
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class TranscriptConfig:
    """Where and how transcript lines are posted back into Discord."""
    # 0 = post to the channel the command was used in
    channel_id: int = field(
        default_factory=lambda: _env_int("TRANSCRIPT_CHANNEL_ID", 0)
    )
    # Batch window: utterances arriving inside this window are grouped
    flush_interval_seconds: float = field(
        default_factory=lambda: _env_float("TRANSCRIPT_FLUSH_INTERVAL", 2.0)
    )
    max_lines_per_message: int = field(
        default_factory=lambda: _env_int("TRANSCRIPT_MAX_LINES", 12)
    )
    # Local copy of each finished session transcript
    save_dir: str = field(
        default_factory=lambda: _env_str("TRANSCRIPT_SAVE_DIR", "transcripts")
    )


# ──────────────────────────────────────────────
# Singleton instances (import these directly)
# ──────────────────────────────────────────────
discord_cfg = DiscordConfig()
audio_cfg = AudioConfig()
vad_cfg = VADConfig()
stt_cfg = STTConfig()
api_cfg = APIConfig()
cleanup_cfg = CleanupConfig()
transcript_cfg = TranscriptConfig()
process_cfg = ProcessConfig()
log_cfg = LogConfig()
