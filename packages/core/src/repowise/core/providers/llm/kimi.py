"""Kimi (Moonshot) "Kimi For Coding" provider for repowise.

Access Kimi coding models via the Kimi For Coding API at
https://api.kimi.com/coding/v1. The API is OpenAI-compatible — this provider
uses the openai Python SDK with a custom base_url, following the same pattern
as DeepSeekProvider.

The one Kimi-specific detail: the coding endpoint is gated to coding agents and
rejects generic clients with::

    {"error":{"message":"Kimi For Coding is currently only available for Coding
     Agents such as Kimi CLI, Claude Code, Roo Code, Kilo Code, etc.",
     "type":"access_terminated_error"}}

We pass a coding-agent ``User-Agent`` (overridable via KIMI_USER_AGENT) through
the OpenAI client's ``default_headers`` to satisfy that gate.

Models:
    - kimi-k2.7-code  — Kimi coding model [default]
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog
from openai import APIStatusError as _OpenAIAPIStatusError
from openai import AsyncOpenAI
from openai import Omit as _OpenAIOmit
from openai import RateLimitError as _OpenAIRateLimitError
from tenacity import RetryError, retry

from repowise.core.providers.llm.base import (
    BaseProvider,
    ChatStreamEvent,
    ChatToolCall,
    GeneratedResponse,
    ProviderError,
    ProviderModelOption,
    RateLimitError,
    fallback_model_option,
    parse_retry_after,
    provider_retry_stop,
    provider_retry_wait,
    provider_should_retry,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"
_DEFAULT_MODEL = "kimi-k2.7-code"
# Coding-agent User-Agent required by the Kimi For Coding gate. Override via
# KIMI_USER_AGENT if Kimi rotates the accepted client list.
_DEFAULT_USER_AGENT = "KimiCLI/1.3"


def _kimi_headers(user_agent: str) -> dict[str, Any]:
    """Headers that make the OpenAI SDK look like an approved coding agent.

    Setting the ``x-stainless-*`` markers to the SDK's ``Omit`` sentinel removes
    the auto-injected client fingerprint so only our coding-agent User-Agent is
    advertised — Kimi gates on the latter. (A plain ``None`` value would raise
    in httpx at request-build time; ``Omit`` is the SDK's supported way to drop
    a default header.)
    """
    # Keys must match the SDK's exact casing (X-Stainless-*); the default-header
    # merge is a plain dict update, so a casing mismatch silently fails to
    # override the platform value.
    omit = _OpenAIOmit()
    return {
        "User-Agent": user_agent,
        "X-Stainless-Lang": omit,
        "X-Stainless-Package-Version": omit,
        "X-Stainless-OS": omit,
        "X-Stainless-Arch": omit,
        "X-Stainless-Runtime": omit,
        "X-Stainless-Runtime-Version": omit,
    }


def _kimi_model_options(
    api_key: str,
    base_url: str,
    user_agent: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    fallback = fallback_model_option(fallback_model, reasoning_modes=("auto",))
    try:
        import httpx

        response = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": user_agent},
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
    except Exception:
        return (fallback,)

    if not isinstance(data, list):
        return (fallback,)

    options: list[ProviderModelOption] = []
    for raw in data:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        model_id = raw["id"]
        options.append(
            ProviderModelOption(
                model=model_id,
                label=model_id,
                reasoning_modes=("auto",),
                recommended=model_id == fallback_model,
                source="api",
                notes="",
            )
        )

    if not options:
        return (fallback,)

    return tuple(options)


class KimiProvider(BaseProvider):
    """Kimi For Coding provider — access Kimi models via OpenAI-compatible API.

    Args:
        api_key:      Kimi API key. Falls back to KIMI_API_KEY env var.
        model:        Model identifier. Defaults to kimi-k2.7-code.
        base_url:     Override the Kimi API URL (KIMI_BASE_URL env var).
        user_agent:   Coding-agent User-Agent to bypass the access gate
                      (KIMI_USER_AGENT env var).
        rate_limiter: Optional RateLimiter instance.
        cost_tracker: Optional CostTracker instance for usage recording.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        base_url: str | None = None,
        user_agent: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("KIMI_API_KEY")
        if not resolved_key:
            raise ProviderError(
                "kimi",
                "No API key provided. Pass api_key= or set KIMI_API_KEY.",
            )
        resolved_base_url = base_url or os.environ.get("KIMI_BASE_URL") or _DEFAULT_BASE_URL
        resolved_user_agent = (
            user_agent or os.environ.get("KIMI_USER_AGENT") or _DEFAULT_USER_AGENT
        )
        self._api_key = resolved_key
        self._base_url = resolved_base_url
        self._user_agent = resolved_user_agent
        self._client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=resolved_base_url,
            default_headers=_kimi_headers(resolved_user_agent),
        )
        self._model = model
        self._rate_limiter = rate_limiter
        self._cost_tracker = cost_tracker

    @property
    def provider_name(self) -> str:
        return "kimi"

    @property
    def model_name(self) -> str:
        return self._model

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto",)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _kimi_model_options(
            self._api_key, self._base_url, self._user_agent, self._model
        )

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        request_id: str | None = None,
        reasoning: ReasoningMode = "auto",
        cache_hints: tuple = (),
    ) -> GeneratedResponse:
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "kimi.generate.start",
            model=self._model,
            max_tokens=max_tokens,
            request_id=request_id,
        )

        try:
            return await self._generate_with_retry(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                request_id=request_id,
            )
        except RetryError as exc:
            raise ProviderError(
                "kimi",
                f"All retries exhausted: {exc}",
            ) from exc

    @retry(
        retry=provider_should_retry,
        stop=provider_retry_stop,
        wait=provider_retry_wait,
        reraise=True,
    )
    async def _generate_with_retry(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        request_id: str | None,
    ) -> GeneratedResponse:
        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            response = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError(
                "kimi",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("kimi", str(exc), status_code=exc.status_code) from exc

        choice = response.choices[0]
        usage = response.usage
        cached = 0
        if usage is not None:
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
        content = choice.message.content or ""

        # kimi-k2.7-code is a reasoning model: its reasoning_content shares the
        # max_tokens budget with the answer, so a too-small budget can exhaust
        # the limit before any answer is emitted (finish_reason == "length",
        # content == ""). Surface that instead of silently returning a blank
        # page.
        if getattr(choice, "finish_reason", None) == "length":
            log.warning(
                "kimi.generate.truncated",
                model=self._model,
                max_tokens=max_tokens,
                output_tokens=usage.completion_tokens if usage else 0,
                content_empty=not content,
                request_id=request_id,
                hint="raise max_tokens — reasoning_content shares the output budget",
            )

        result = GeneratedResponse(
            content=content,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=cached,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
                "cached_tokens": cached,
            },
        )
        log.debug(
            "kimi.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cached_tokens=result.cached_tokens,
            request_id=request_id,
        )

        if self._cost_tracker is not None:
            # Await the cost record inline rather than spawning a detached task;
            # see DeepSeekProvider for the "Event loop is closed" rationale.
            with contextlib.suppress(Exception):
                await self._cost_tracker.record(
                    model=self._model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    operation="doc_generation",
                    file_path=None,
                )

        return result

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        request_id: str | None = None,
        tool_executor: Any | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        import json as _json

        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": full_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError(
                "kimi",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("kimi", str(exc), status_code=exc.status_code) from exc

        tool_calls_acc: dict[int, dict[str, Any]] = {}

        try:
            async for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    if chunk.usage:
                        yield ChatStreamEvent(
                            type="usage",
                            input_tokens=chunk.usage.prompt_tokens or 0,
                            output_tokens=chunk.usage.completion_tokens or 0,
                        )
                    continue

                delta = choice.delta
                finish = choice.finish_reason

                if delta and delta.content:
                    yield ChatStreamEvent(type="text_delta", text=delta.content)

                if delta and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc_delta.id or "",
                                "name": "",
                                "arguments": "",
                            }
                        acc = tool_calls_acc[idx]
                        if tc_delta.id:
                            acc["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                acc["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                acc["arguments"] += tc_delta.function.arguments

                if finish:
                    for idx in sorted(tool_calls_acc.keys()):
                        acc = tool_calls_acc[idx]
                        try:
                            args = _json.loads(acc["arguments"]) if acc["arguments"] else {}
                        except Exception:
                            args = {}
                        yield ChatStreamEvent(
                            type="tool_start",
                            tool_call=ChatToolCall(
                                id=acc["id"],
                                name=acc["name"],
                                arguments=args,
                            ),
                        )
                    tool_calls_acc.clear()

                    stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"
                    yield ChatStreamEvent(type="stop", stop_reason=stop_reason)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError(
                "kimi",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("kimi", str(exc), status_code=exc.status_code) from exc
