"""LLM abstraction with offline and Anthropic implementations."""

from __future__ import annotations

import inspect
import json
import os
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel

from dsbot.config import load_settings
from dsbot.db import get_db
from dsbot.redact import redact_text

_USAGE_LOCK = threading.Lock()


class LLMBudgetExceeded(RuntimeError):
    """Raised when today's configured LLM budget has paused model requests."""


def _estimate_tokens(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def _usage_for_day(day: date) -> dict[str, Any]:
    store = get_db()
    row = store.fetch_one(
        "SELECT token_count, cost_usd, paused FROM llm_daily_usage WHERE usage_date = ?",
        (day.isoformat(),),
    )
    if row is not None:
        return row
    store.execute(
        "INSERT OR IGNORE INTO llm_daily_usage (usage_date, token_count, cost_usd, paused) "
        "VALUES (?, 0, 0, 0)",
        (day.isoformat(),),
    )
    return {"token_count": 0, "cost_usd": 0.0, "paused": 0}


def _set_usage(day: date, tokens: int, cost: float, paused: bool) -> None:
    get_db().execute(
        "INSERT INTO llm_daily_usage (usage_date, token_count, cost_usd, paused) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(usage_date) DO UPDATE SET "
        "token_count = excluded.token_count, cost_usd = excluded.cost_usd, "
        "paused = excluded.paused",
        (day.isoformat(), tokens, cost, int(paused)),
    )


def llm_budget_paused() -> bool:
    """Return whether today's persistent LLM budget has paused model requests."""

    with _USAGE_LOCK:
        return bool(_usage_for_day(date.today()).get("paused"))


def _wait_for_rate_limit(requests_per_minute: int) -> None:
    store = get_db()
    while True:
        now = time.time()
        with store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM llm_request_timestamps WHERE requested_at <= ?",
                (now - 60,),
            )
            oldest = connection.execute(
                "SELECT MIN(requested_at) FROM llm_request_timestamps"
            ).fetchone()[0]
            count = int(
                connection.execute("SELECT COUNT(*) FROM llm_request_timestamps").fetchone()[0]
            )
            if count < requests_per_minute:
                connection.execute(
                    "INSERT INTO llm_request_timestamps (requested_at) VALUES (?)",
                    (now,),
                )
                return
        time.sleep(max(0.01, 60 - (now - float(oldest))))


def _record_usage(
    day: date,
    tokens: int,
    cost: float,
    token_cap: int,
    cost_cap: float,
) -> tuple[int, float, bool, bool]:
    """Atomically add usage and pause once either configured cap is reached."""

    with get_db().connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO llm_daily_usage (usage_date, token_count, cost_usd, paused) "
            "VALUES (?, ?, ?, 0) ON CONFLICT(usage_date) DO UPDATE SET "
            "token_count = token_count + excluded.token_count, "
            "cost_usd = cost_usd + excluded.cost_usd",
            (day.isoformat(), tokens, cost),
        )
        row = connection.execute(
            "SELECT token_count, cost_usd, paused FROM llm_daily_usage WHERE usage_date = ?",
            (day.isoformat(),),
        ).fetchone()
        total_tokens, total_cost, already_paused = int(row[0]), float(row[1]), bool(row[2])
        exceeded = (bool(token_cap) and total_tokens >= token_cap) or (
            bool(cost_cap) and total_cost >= cost_cap
        )
        if exceeded and not already_paused:
            connection.execute(
                "UPDATE llm_daily_usage SET paused = 1 WHERE usage_date = ?",
                (day.isoformat(),),
            )
        return total_tokens, total_cost, exceeded, exceeded and not already_paused


class LLMMessage(BaseModel):
    """Compatibility model for callers from the initial scaffold."""

    role: str
    content: str


class LLMResponse(BaseModel):
    """Compatibility model for callers from the initial scaffold."""

    content: str
    model: str
    usage: dict[str, int] | None = None


class LLM(ABC):
    """Common interface for text completions."""

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        """Redact prompts, enforce daily limits, and account for each model call."""
        settings = load_settings()
        safe_system = redact_text(system, token_prefix="SYSTEM_")
        safe_user = redact_text(user, token_prefix="USER_")
        input_tokens = _estimate_tokens(safe_system.text) + _estimate_tokens(safe_user.text)
        today = date.today()

        with _USAGE_LOCK:
            usage = _usage_for_day(today)
            token_cap = settings.llm_daily_token_cap
            cost_cap = settings.llm_daily_cost_cap_usd
            if usage["paused"]:
                raise LLMBudgetExceeded("Daily LLM budget is paused")
            if (
                (token_cap and int(usage["token_count"]) >= token_cap)
                or (cost_cap and float(usage["cost_usd"]) >= cost_cap)
            ):
                _set_usage(
                    today,
                    int(usage["token_count"]),
                    float(usage["cost_usd"]),
                    True,
                )
                from dsbot.audit import log

                log("bot", "llm_budget_paused", date=today.isoformat(), reason="daily_cap_reached")
                raise LLMBudgetExceeded("Daily LLM token or cost cap reached")

        retries = getattr(self, "retries", settings.llm_max_retries)
        backoff = getattr(self, "backoff_base_seconds", settings.llm_backoff_base_seconds)
        for attempt in range(retries + 1):
            _wait_for_rate_limit(settings.llm_requests_per_minute)
            try:
                response = self._complete(safe_system.text, safe_user.text, json_mode)
                break
            except Exception:
                if attempt == retries:
                    raise
                time.sleep(backoff * (2**attempt))
        total_tokens = input_tokens + _estimate_tokens(response)
        cost = total_tokens * settings.llm_cost_per_million_tokens_usd / 1_000_000
        total_day_tokens, total_day_cost, exceeded, newly_paused = _record_usage(
            today,
            total_tokens,
            cost,
            settings.llm_daily_token_cap,
            settings.llm_daily_cost_cap_usd,
        )
        if newly_paused:
            from dsbot.audit import log

            log(
                "bot",
                "llm_budget_paused",
                date=today.isoformat(),
                tokens=total_day_tokens,
                cost_usd=round(total_day_cost, 8),
                reason="daily_cap_exceeded",
            )
        if exceeded:
            raise LLMBudgetExceeded("Daily LLM token or cost cap exceeded")
        return safe_user.restore(safe_system.restore(response))

    @abstractmethod
    def _complete(self, system: str, user: str, json_mode: bool = False) -> str:
        """Send already-redacted text to the configured model backend."""

    @abstractmethod
    def get_model_name(self) -> str:
        """Return the configured model name."""


class FakeLLM(LLM):
    """Deterministic LLM for tests and local mock-mode execution."""

    def __init__(
        self,
        responses: Mapping[Any, str] | Callable[..., str] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.calls: list[dict[str, Any]] = []
        self.call_log = self.calls

    @property
    def has_canned_responses(self) -> bool:
        """Whether this fake has an explicit response source configured."""

        return bool(self.responses)

    def _call_response(self, system: str, user: str, json_mode: bool) -> str:
        if callable(self.responses):
            provider = self.responses
            try:
                parameters = inspect.signature(provider).parameters.values()
                positional = [
                    parameter
                    for parameter in parameters
                    if parameter.kind
                    in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                ]
                accepts_varargs = any(
                    parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters
                )
            except (TypeError, ValueError):
                positional = []
                accepts_varargs = True

            if accepts_varargs or len(positional) >= 3:
                result = provider(system, user, json_mode)
            elif len(positional) == 2:
                result = provider(system, user)
            else:
                result = provider(user)
            return str(result)

        responses = self.responses
        if not isinstance(responses, Mapping):
            return "Fake LLM response"

        for key in ((system, user, json_mode), (system, user), user, "default", "__default__"):
            if key in responses:
                return str(responses[key])
        return "Fake LLM response"

    def _complete(self, system: str, user: str, json_mode: bool = False) -> str:
        """Return a canned response and record the redacted prompt."""

        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        return self._call_response(system, user, json_mode)

    def get_model_name(self) -> str:
        return "fake-model"


class AnthropicLLM(LLM):
    """Anthropic-backed completion client with bounded retries."""

    def __init__(
        self,
        model: str | None = None,
        timeout: float = 30.0,
        retries: int | None = None,
        client: Any | None = None,
    ) -> None:
        settings = load_settings()
        self.model = (
            model
            or os.getenv("ANTHROPIC_MODEL")
            or os.getenv("AGENT_MODEL")
            or settings.agent_model
        )
        self.timeout = timeout
        self.retries = max(0, settings.llm_max_retries if retries is None else retries)
        self.client = client or self._create_client(
            settings.anthropic_api_key,
            settings.anthropic_base_url,
        )

    def _create_client(self, api_key: str | None, base_url: str | None) -> Any:
        try:
            import anthropic
        except ImportError as error:  # pragma: no cover - dependency is in pyproject.toml
            raise RuntimeError(
                "anthropic package not installed; install project dependencies"
            ) from error

        client_options: dict[str, Any] = {"timeout": self.timeout, "max_retries": 0}
        if base_url:
            client_options["base_url"] = base_url
        if os.getenv("OPENROUTER_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
            client_options["auth_token"] = api_key
        else:
            client_options["api_key"] = api_key
        return anthropic.Anthropic(**client_options)

    def _complete(self, system: str, user: str, json_mode: bool = False) -> str:
        """Send one already-redacted request to Anthropic."""

        system_prompt = system
        if json_mode:
            system_prompt = f"{system}\nReturn only valid JSON with no markdown fences."

        response = self.client.messages.create(
            model=self.model,
            system=system_prompt,
            messages=[{"role": "user", "content": user}],
            max_tokens=4096,
            timeout=self.timeout,
        )
        return self._extract_text(response)

    @staticmethod
    def _extract_text(response: Any) -> str:
        content = getattr(response, "content", None)
        if not content:
            return ""
        first = content[0]
        if isinstance(first, dict):
            return str(first.get("text", ""))
        return str(getattr(first, "text", first))

    def get_model_name(self) -> str:
        return self.model


class OpenRouterLLM(LLM):
    """OpenAI-compatible chat-completions client for OpenRouter."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 30.0,
        retries: int = 2,
    ) -> None:
        if not api_key:
            raise ValueError("OpenRouter API key is required")
        self._api_key = api_key
        self.model = model
        self.timeout = timeout
        self.retries = max(0, retries)
        endpoint = (base_url or "https://openrouter.ai/api").rstrip("/")
        if endpoint.endswith("/chat/completions"):
            self.endpoint = endpoint
        elif endpoint.endswith("/v1"):
            self.endpoint = f"{endpoint}/chat/completions"
        else:
            self.endpoint = f"{endpoint}/v1/chat/completions"

    def _complete(self, system: str, user: str, json_mode: bool = False) -> str:
        """Make one OpenRouter request, keeping credentials out of errors/logging."""

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 4096,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise RuntimeError(f"OpenRouter request failed with HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError):
            raise RuntimeError("OpenRouter request failed") from None
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError("OpenRouter returned an invalid response") from None

        try:
            content = response_data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("OpenRouter response did not contain a completion") from None
        if not isinstance(content, str):
            raise RuntimeError("OpenRouter response contained invalid completion content")
        return content

    def get_model_name(self) -> str:
        return self.model


# Existing feature modules imported this name before the core API was added.
BaseLLM = LLM


def get_llm(testing: bool | None = None, **kwargs: Any) -> LLM:
    """Return a fake client without credentials or the configured live provider.

    ``testing=True`` remains supported for the existing CLI. Setting
    ``DSBOT_MODE=mock`` explicitly also forces the offline implementation.
    OpenRouter credentials select its OpenAI-compatible chat-completions API.
    """

    settings = load_settings()
    api_key = (
        os.getenv("OPENROUTER_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ANTHROPIC_API_KEY")
    )
    explicit_mock_mode = os.getenv("DSBOT_MODE", "").lower() == "mock"
    if testing is True or not api_key or explicit_mock_mode:
        return FakeLLM(kwargs.pop("responses", None))

    model = kwargs.pop("model", None) or settings.agent_model
    if os.getenv("OPENROUTER_API_KEY"):
        return OpenRouterLLM(
            api_key=os.environ["OPENROUTER_API_KEY"],
            model=model,
            base_url=os.getenv("OPENROUTER_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL"),
            timeout=kwargs.pop("timeout", 30.0),
            retries=kwargs.pop("retries", 2),
        )
    return AnthropicLLM(model=model, **kwargs)
