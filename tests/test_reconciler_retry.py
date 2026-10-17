"""A stalled batch is retried, not given up on.

The endpoint is free and intermittent, so a timeout is normal operation. Giving
up costs the reader the reviewed transcript; retrying costs a delay, and the
delay is announced.
"""

from stt import reconciler


def test_a_stalled_batch_is_retried_until_it_answers(monkeypatch):
    calls = {"n": 0}
    notices = []

    def fake_once(part, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return reconciler.Reconciled(error="The read operation timed out")
        return reconciler.Reconciled(replacements={1: "settled"})

    monkeypatch.setattr(reconciler, "_once", fake_once)
    monkeypatch.setattr(reconciler, "BACKOFF", 0)

    result = reconciler.reconcile(
        ["1. battle (shop | shock)"], "u", "k", "m", 1.0,
        lambda reason, attempt: notices.append(attempt),
    )

    assert result.replacements == {1: "settled"}
    assert calls["n"] == 3
    assert notices == [1, 2], "the channel is told before each wait"


def test_a_notice_that_raises_does_not_stop_the_retry(monkeypatch):
    calls = {"n": 0}

    def fake_once(part, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            return reconciler.Reconciled(error="timeout")
        return reconciler.Reconciled(replacements={1: "done"})

    monkeypatch.setattr(reconciler, "_once", fake_once)
    monkeypatch.setattr(reconciler, "BACKOFF", 0)

    def broken_notice(reason, attempt):
        raise RuntimeError("discord is down too")

    result = reconciler.reconcile(["1. a | b"], "u", "k", "m", 1.0, broken_notice)
    assert result.replacements == {1: "done"}


def test_a_busy_model_falls_through_to_the_next(monkeypatch):
    """The list is tried in order, so a busy 6.8 can hand over to a fallback."""
    tried = []

    def fake_once(part, base_url, api_key, model, timeout=0, effort=""):
        tried.append(model)
        if model == "busy":
            return reconciler.Reconciled(error="HTTP 429 Server is busy")
        return reconciler.Reconciled(replacements={1: "settled"})

    monkeypatch.setattr(reconciler, "_once", fake_once)
    result = reconciler.reconcile(["1. a | b"], "u", "k", ["busy", "backup"], 1.0)

    assert result.replacements == {1: "settled"}
    assert tried == ["busy", "backup"], "the first model is tried before the second"


def test_every_model_failing_waits_and_tries_them_again(monkeypatch):
    attempts = []
    state = {"n": 0}

    def fake_once(part, base_url, api_key, model, timeout=0, effort=""):
        attempts.append(model)
        state["n"] += 1
        if state["n"] <= 2:
            return reconciler.Reconciled(error="HTTP 429")
        return reconciler.Reconciled(replacements={1: "done"})

    monkeypatch.setattr(reconciler, "_once", fake_once)
    monkeypatch.setattr(reconciler, "BACKOFF", 0)
    result = reconciler.reconcile(["1. a | b"], "u", "k", ["m1", "m2"], 1.0)

    assert result.replacements == {1: "done"}
    assert attempts == ["m1", "m2", "m1"]


def test_a_refused_key_fails_fast(monkeypatch):
    """An auth error is a configuration mistake, not a busy endpoint."""
    calls = []

    def fake_once(part, base_url, api_key, model, timeout=0, effort=""):
        calls.append(model)
        return reconciler.Reconciled(error="HTTP 401")

    monkeypatch.setattr(reconciler, "_once", fake_once)
    monkeypatch.setattr(reconciler, "BACKOFF", 0)
    result = reconciler.reconcile(["1. a | b"], "u", "wrong", ["m1", "m2"], 1.0)

    assert result.error == "HTTP 401"
    assert calls == ["m1"], "the second model is not tried and nothing is retried"
