"""core/llm.py: the fallback chain against stand-in providers, and the Anthropic SDK request/response shape
against a local HTTP stand-in (httpx2.MockTransport). No real model is called; see the sprint report."""

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

import anthropic
import httpx2
import pytest
from pydantic import BaseModel
from sqlalchemy import text

from core.db import tenant_tx
from core.errors import NotConfigured
from core.llm import (
    LLM,
    AnthropicProvider,
    Completion,
    LLMUnavailable,
    ProviderError,
    Route,
    estimate_cost,
    get_llm,
    parse_routes,
)
from tests.conftest import SeededTenant


class Answer(BaseModel):
    value: str


class ScriptedProvider:
    """Plays back a fixed sequence of outcomes, one per call."""

    name = "scripted"

    def __init__(self, outcomes: list[Exception | str]) -> None:
        self.outcomes = outcomes
        self.models: list[str] = []

    async def complete(
        self, model: str, system: str, user: str, output_type: Any, max_tokens: int
    ) -> Completion[Any]:
        self.models.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return Completion(parsed=output_type(value=outcome), input_tokens=1000, output_tokens=200)


async def runs(tenant_id: UUID) -> list[tuple[str, str, str | None]]:
    async with tenant_tx(tenant_id) as s:
        rows = await s.execute(
            text("SELECT model, status, prompt_version FROM agent_runs ORDER BY created_at")
        )
        return [(r.model, r.status, r.prompt_version) for r in rows]


def chain(provider: ScriptedProvider, *models: str) -> LLM:
    return LLM([Route("scripted", m) for m in models], {"scripted": provider})


async def call(llm: LLM, tenant: SeededTenant) -> Any:
    return await llm.structured(
        tenant_id=tenant.id, agent="test", prompt_version="test_v1", system="s", user="u", output_type=Answer
    )


async def test_first_route_ok(fresh_tenant: SeededTenant) -> None:
    result = await call(chain(ScriptedProvider(["yes"]), "m1", "m2"), fresh_tenant)
    assert (result.parsed.value, result.route.model, result.fallback) == ("yes", "m1", False)
    assert await runs(fresh_tenant.id) == [("m1", "ok", "test_v1")]


async def test_retryable_errors_fall_back_and_are_recorded(fresh_tenant: SeededTenant) -> None:
    provider = ScriptedProvider(
        [ProviderError("HTTP 429", retryable=True), ProviderError("timeout", retryable=True), "ok"]
    )
    result = await call(chain(provider, "m1", "m2", "m3"), fresh_tenant)
    assert result.route.model == "m3" and result.fallback
    assert await runs(fresh_tenant.id) == [
        ("m1", "error", "test_v1"),
        ("m2", "error", "test_v1"),
        ("m3", "fallback", "test_v1"),
    ]


async def test_non_retryable_error_stops_the_chain(fresh_tenant: SeededTenant) -> None:
    provider = ScriptedProvider([ProviderError("HTTP 400", retryable=False), "never"])
    with pytest.raises(ProviderError):
        await call(chain(provider, "m1", "m2"), fresh_tenant)
    assert provider.models == ["m1"]


async def test_all_routes_failing_raises(fresh_tenant: SeededTenant) -> None:
    provider = ScriptedProvider([ProviderError("HTTP 503", retryable=True)] * 2)
    with pytest.raises(LLMUnavailable, match=r"m1.*m2"):
        await call(chain(provider, "m1", "m2"), fresh_tenant)


def test_routes_costs_and_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    assert parse_routes("anthropic:claude-opus-5-5, anthropic:claude-sonnet-5-5") == [
        Route("anthropic", "claude-opus-5-5"),
        Route("anthropic", "claude-sonnet-5-5"),
    ]
    for bad in ("", "anthropic", ":model"):
        with pytest.raises(ValueError):
            parse_routes(bad)
    assert estimate_cost("claude-opus-5-5", 1_000_000, 100_000) == Decimal("6")
    assert estimate_cost("unknown-model", 10, 10) is None
    with pytest.raises(NotConfigured):
        LLM([Route("other", "m")], {})
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    with pytest.raises(NotConfigured, match="ANTHROPIC_API_KEY"):
        get_llm()


def anthropic_stub(handler: Any) -> AnthropicProvider:
    client = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    return AnthropicProvider("test-key", "https://api.anthropic.test", 5.0, http_client=client)


def message(content: list[dict[str, Any]], stop: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


async def test_anthropic_provider_sends_structured_output_request() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        seen["path"] = request.url.path
        return httpx2.Response(200, json=message([{"type": "text", "text": '{"value": "parsed"}'}]))

    result = await anthropic_stub(handler).complete("claude-opus-5-5", "SYSTEM", "USER", Answer, 1000)
    assert result.parsed == Answer(value="parsed") and (result.input_tokens, result.output_tokens) == (
        120,
        30,
    )
    assert (
        seen["path"] == "/v1/messages" and seen["model"] == "claude-opus-5-5" and seen["system"] == "SYSTEM"
    )
    assert seen["messages"] == [{"role": "user", "content": "USER"}]
    assert seen["output_config"]["format"]["type"] == "json_schema"
    assert "tool_choice" not in seen


@pytest.mark.parametrize(
    ("status", "retryable"), [(429, True), (529, True), (500, True), (400, False), (401, False)]
)
async def test_anthropic_provider_error_mapping(status: int, retryable: bool) -> None:
    provider = anthropic_stub(
        lambda r: httpx2.Response(status, json={"type": "error", "error": {"type": "x", "message": "m"}})
    )
    with pytest.raises(ProviderError) as exc:
        await provider.complete("claude-opus-5-5", "s", "u", Answer, 100)
    assert exc.value.retryable is retryable


async def test_anthropic_refusal_is_retryable() -> None:
    provider = anthropic_stub(lambda r: httpx2.Response(200, json=message([], stop="refusal")))
    with pytest.raises(ProviderError, match="refused") as exc:
        await provider.complete("claude-opus-5-5", "s", "u", Answer, 100)
    assert exc.value.retryable
