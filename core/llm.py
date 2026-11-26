"""Every LLM call goes through here: ordered provider/model fallback, Pydantic structured output, and one
`agent_runs` row per attempt (model, provider, prompt_version, tokens, latency, cost, status).

Fallback happens only on timeout, 429, 5xx, connection errors and refusals; a 4xx such as a bad request or bad
key fails immediately. A call served by any route other than the first is recorded with status 'fallback'.
"""

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, Protocol, TypeVar
from uuid import UUID

import anthropic
from pydantic import BaseModel
from sqlalchemy import text

from core.config import get_settings
from core.db import tenant_tx
from core.errors import NotConfigured

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# USD per million tokens (input, output). Unknown models are logged with cost NULL, never guessed.
PRICES_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5-5": (Decimal("4.00"), Decimal("20.00")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
}


@dataclass(frozen=True)
class Route:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class Completion(Generic[T]):
    parsed: T
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    parsed: T
    route: Route
    run_id: UUID
    fallback: bool


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMUnavailable(Exception):
    """Every route failed with a retryable error."""


class Provider(Protocol):
    name: str

    async def complete(
        self, model: str, system: str, user: str, output_type: type[T], max_tokens: int
    ) -> Completion[T]: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: float,
        http_client: anthropic.DefaultAsyncHttpxClient | None = None,
    ) -> None:
        # Retries are ours (next route), so the SDK makes one attempt per route.
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0, http_client=http_client
        )

    async def complete(
        self, model: str, system: str, user: str, output_type: type[T], max_tokens: int
    ) -> Completion[T]:
        try:
            response = await self._client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_type,
            )
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            retryable = exc.status_code == 429 or exc.status_code >= 500
            raise ProviderError(f"HTTP {exc.status_code}: {exc.message}", retryable=retryable) from exc
        if response.stop_reason == "refusal":
            raise ProviderError("model refused", retryable=True)
        if response.parsed_output is None:
            raise ProviderError(f"no structured output (stop_reason={response.stop_reason})", retryable=True)
        return Completion(
            parsed=response.parsed_output,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


def parse_routes(spec: str) -> list[Route]:
    routes = []
    for part in (p.strip() for p in spec.split(",")):
        provider, sep, model = part.partition(":")
        if not sep or not provider or not model:
            raise ValueError(f"invalid LLM route {part!r}; expected provider:model")
        routes.append(Route(provider, model))
    if not routes:
        raise ValueError("LLM_ROUTES is empty")
    return routes


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    price = PRICES_PER_MTOK.get(model)
    if price is None:
        return None
    return (price[0] * input_tokens + price[1] * output_tokens) / Decimal(1_000_000)


class LLM:
    def __init__(self, routes: list[Route], providers: dict[str, Provider]) -> None:
        missing = {r.provider for r in routes} - providers.keys()
        if missing:
            raise NotConfigured(f"no provider configured for: {', '.join(sorted(missing))}")
        self.routes = routes
        self._providers = providers

    async def structured(
        self,
        *,
        tenant_id: UUID,
        agent: str,
        prompt_version: str,
        system: str,
        user: str,
        output_type: type[T],
        max_tokens: int = 16000,
    ) -> LLMResult[T]:
        errors: list[str] = []
        for index, route in enumerate(self.routes):
            started = time.perf_counter()
            try:
                result = await self._providers[route.provider].complete(
                    route.model, system, user, output_type, max_tokens
                )
            except ProviderError as exc:
                latency = int((time.perf_counter() - started) * 1000)
                await _record(tenant_id, agent, route, prompt_version, "error", latency, error=str(exc))
                log.warning("llm.attempt_failed", extra={"route": str(route), "error": str(exc)})
                if not exc.retryable:
                    raise
                errors.append(f"{route}: {exc}")
                continue
            latency = int((time.perf_counter() - started) * 1000)
            status = "ok" if index == 0 else "fallback"
            run_id = await _record(
                tenant_id,
                agent,
                route,
                prompt_version,
                status,
                latency,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            if index:
                log.warning("llm.served_by_fallback", extra={"route": str(route), "agent": agent})
            return LLMResult(parsed=result.parsed, route=route, run_id=run_id, fallback=index > 0)
        raise LLMUnavailable("; ".join(errors))


async def _record(
    tenant_id: UUID,
    agent: str,
    route: Route,
    prompt_version: str,
    status: str,
    latency_ms: int,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    error: str | None = None,
) -> UUID:
    cost = (
        estimate_cost(route.model, input_tokens, output_tokens)
        if input_tokens is not None and output_tokens is not None
        else None
    )
    async with tenant_tx(tenant_id) as s:
        run_id = await s.scalar(
            text("""
                INSERT INTO agent_runs (tenant_id, agent, provider, model, prompt_version, input_tokens,
                    output_tokens, latency_ms, cost_usd, status, error)
                VALUES (:t, :agent, :provider, :model, :pv, :inp, :out, :lat, :cost, :status, :error)
                RETURNING id
            """),
            {
                "t": tenant_id,
                "agent": agent,
                "provider": route.provider,
                "model": route.model,
                "pv": prompt_version,
                "inp": input_tokens,
                "out": output_tokens,
                "lat": latency_ms,
                "cost": cost,
                "status": status,
                "error": (error or "")[:2000] or None,
            },
        )
    return UUID(str(run_id))


def get_llm(routes_spec: str | None = None) -> LLM:
    settings = get_settings()
    routes = parse_routes(routes_spec or settings.llm_routes)
    providers: dict[str, Provider] = {}
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    if key:
        providers["anthropic"] = AnthropicProvider(
            key, settings.anthropic_api_base, settings.llm_timeout_seconds
        )
    elif any(r.provider == "anthropic" for r in routes):
        raise NotConfigured("ANTHROPIC_API_KEY is not set")
    return LLM(routes, providers)
