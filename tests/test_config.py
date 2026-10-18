import importlib

import pytest


def test_config_reads_environment_values(monkeypatch) -> None:
    monkeypatch.setenv("COMMAND_PREFIX", "?")
    monkeypatch.setenv("STT_LANGUAGE", "en")
    monkeypatch.setenv("AUDIO_QUEUE_MAXSIZE", "42")

    import config

    reloaded = importlib.reload(config)

    assert reloaded.discord_cfg.command_prefix == "?"
    assert reloaded.stt_cfg.language == "en"
    assert reloaded.process_cfg.audio_queue_maxsize == 42


def test_invalid_integer_config_fails_fast(monkeypatch) -> None:
    monkeypatch.setenv("AUDIO_QUEUE_MAXSIZE", "not-an-int")

    import config

    with pytest.raises(ValueError) as exc:
        config._env_int("AUDIO_QUEUE_MAXSIZE", 1)

    assert "AUDIO_QUEUE_MAXSIZE must be an integer" in str(exc.value)


# -- where a secret comes from ------------------------------------------------


def test_a_systemd_credential_wins_over_the_environment(tmp_path, monkeypatch) -> None:
    """On a deployed host the managed secret is the one that counts."""
    (tmp_path / "openrouter").write_text("sk-or-v1-from-credential\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-from-env")

    import config

    assert config._secret("OPENROUTER_API_KEY", "openrouter") == "sk-or-v1-from-credential"


def test_without_a_credential_directory_the_environment_is_used(monkeypatch) -> None:
    """A workstation has no CREDENTIALS_DIRECTORY; .env and the widget do the rest."""
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-from-env")

    import config

    assert config._secret("OPENROUTER_API_KEY", "openrouter") == "sk-or-v1-from-env"


def test_a_missing_credential_falls_through_rather_than_refusing_to_boot(
    tmp_path, monkeypatch
) -> None:
    """A unit must start and name what is missing, not fail with no explanation."""
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("DISCORD_TOKEN", "from-env")

    import config

    assert config._secret("DISCORD_TOKEN", "discord-token") == "from-env"


def test_a_credential_file_is_stripped(tmp_path, monkeypatch) -> None:
    (tmp_path / "discord-token").write_text("  abc.def.ghi  \n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    import config

    assert config._secret("DISCORD_TOKEN", "discord-token") == "abc.def.ghi"


def test_no_credential_and_no_variable_is_empty(monkeypatch) -> None:
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    import config

    assert config._secret("DISCORD_TOKEN", "discord-token") == ""
