"""Offline OpenRouter client tests with mocked HTTP."""

import json
from datetime import UTC, datetime
from io import BytesIO

from dsbot.cli import _print_llm_status
from dsbot.config import load_settings
from dsbot.connectors.base import Email
from dsbot.db import SQLiteStore
from dsbot.email.triage import EmailTriage
from dsbot.llm import OpenRouterLLM, get_llm


def test_openrouter_posts_completion_and_reports_live_model(monkeypatch, capsys, tmp_path):
    key = "offline-test-secret"
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    monkeypatch.setenv("AGENT_MODEL", "test/model")
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "db.sqlite"))
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return BytesIO(b'{"choices":[{"message":{"content":"completion"}}]}')

    monkeypatch.setattr("dsbot.llm.urlopen", fake_urlopen)
    client = get_llm()

    assert isinstance(client, OpenRouterLLM)
    assert client.complete("system", "hello") == "completion"
    _print_llm_status(client)
    assert capsys.readouterr().out.strip() == "LLM: LIVE (test/model)"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["authorization"] == f"Bearer {key}"
    assert captured["timeout"] == 30.0
    assert captured["payload"]["model"] == "test/model"


def test_openrouter_retries_twice_on_transport_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-test-secret")
    monkeypatch.setenv("AGENT_MODEL", "test/model")
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("DSBOT_LLM_REQUESTS_PER_MINUTE", "100")
    monkeypatch.setenv("DSBOT_LLM_DAILY_TOKEN_CAP", "0")
    monkeypatch.setenv("DSBOT_LLM_DAILY_COST_CAP_USD", "0")
    attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("offline test transport failure")
        return BytesIO(b'{"choices":[{"message":{"content":"recovered"}}]}')

    monkeypatch.setattr("dsbot.llm.urlopen", fake_urlopen)
    client = OpenRouterLLM("offline-test-secret", "test/model", retries=2)

    assert client.complete("system", "hello") == "recovered"
    assert attempts == 3


def test_openrouter_exhaustion_uses_suspicious_rules_without_logging_key(
    monkeypatch,
    tmp_path,
):
    key = "offline-test-secret"
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    monkeypatch.setenv("AGENT_MODEL", "test/model")
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("DSBOT_LLM_REQUESTS_PER_MINUTE", "100")
    monkeypatch.setenv("DSBOT_LLM_DAILY_TOKEN_CAP", "0")
    monkeypatch.setenv("DSBOT_LLM_DAILY_COST_CAP_USD", "0")
    attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        raise OSError(f"offline failure containing no credential")

    monkeypatch.setattr("dsbot.llm.urlopen", fake_urlopen)
    email = Email(
        id="phish",
        sender="security@outside.example",
        to=["person@company.example"],
        subject="Immediate account verification",
        body="Verify your account or it will be suspended.",
        received_at=datetime.now(UTC),
    )
    triage = EmailTriage(
        OpenRouterLLM(key, "test/model", retries=2),
        settings=load_settings(),
        db=SQLiteStore(tmp_path / "db.sqlite"),
    )

    result = triage.classify(email)
    audit_text = (tmp_path / "db.sqlite").read_bytes().decode("latin-1")

    assert attempts == 3
    assert result.category.value == "suspicious"
    assert result.classification_source == "rules"
    assert result.needs_human_review is True
    assert key not in audit_text
