"""Unit tests for KimiProvider.

Focuses on the Kimi-specific behaviour — the coding-agent User-Agent and the
stripping of the OpenAI SDK's ``X-Stainless-*`` client fingerprint — that lets
the gated "Kimi For Coding" endpoint accept requests. Generation itself reuses
the shared OpenAI-compatible path, so it is only smoke-tested.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

import openai

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError
from repowise.core.providers.llm.kimi import KimiProvider


def test_provider_name():
    p = KimiProvider(api_key="sk-kimi-test")
    assert p.provider_name == "kimi"


def test_default_model():
    p = KimiProvider(api_key="sk-kimi-test")
    assert p.model_name == "kimi-k2.7-code"


def test_default_base_url():
    p = KimiProvider(api_key="sk-kimi-test")
    assert p._base_url == "https://api.kimi.com/coding/v1"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "sk-env-kimi")
    p = KimiProvider()
    assert p.provider_name == "kimi"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        KimiProvider()


def test_base_url_and_user_agent_from_env(monkeypatch):
    monkeypatch.setenv("KIMI_BASE_URL", "https://proxy.example/coding/v1")
    monkeypatch.setenv("KIMI_USER_AGENT", "ClaudeCode/9.9")
    p = KimiProvider(api_key="sk-kimi-test")
    assert p._base_url == "https://proxy.example/coding/v1"
    assert p._user_agent == "ClaudeCode/9.9"


def _outgoing_headers(provider: KimiProvider):
    """Build a request the way the SDK would and return its final headers."""
    req = provider._client._build_request(
        openai._base_client.FinalRequestOptions(
            method="post",
            url="/chat/completions",
            json_data={"model": "x", "messages": []},
        )
    )
    return req.headers


def test_user_agent_overrides_sdk_default():
    p = KimiProvider(api_key="sk-kimi-test", user_agent="KimiCLI/1.3")
    headers = _outgoing_headers(p)
    assert headers.get("user-agent") == "KimiCLI/1.3"
    assert "authorization" in headers


def test_stainless_fingerprint_stripped():
    """The X-Stainless-* client fingerprint must not leak past the gate."""
    p = KimiProvider(api_key="sk-kimi-test")
    headers = _outgoing_headers(p)
    for marker in (
        "x-stainless-lang",
        "x-stainless-package-version",
        "x-stainless-os",
        "x-stainless-arch",
        "x-stainless-runtime",
        "x-stainless-runtime-version",
    ):
        assert marker not in headers, f"{marker} leaked into the request"


def _make_mock_chat_response(
    text: str = "# Doc\nContent.",
    *,
    cached_tokens: int = 0,
    finish_reason: str = "stop",
) -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 50
    usage.total_tokens = 150
    usage.prompt_tokens_details.cached_tokens = cached_tokens

    choice = MagicMock()
    choice.message.content = text
    choice.finish_reason = finish_reason

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


async def test_generate_returns_generated_response():
    provider = KimiProvider(api_key="sk-kimi-test")
    mock_response = _make_mock_chat_response("Hello from Kimi")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        result = await provider.generate(
            system_prompt="You are a test assistant",
            user_prompt="Say hello",
        )

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from Kimi"
    assert result.input_tokens == 100
    assert result.output_tokens == 50

    kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "kimi-k2.7-code"


async def test_generate_captures_cached_tokens():
    provider = KimiProvider(api_key="sk-kimi-test")
    mock_response = _make_mock_chat_response("cached", cached_tokens=16)

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        result = await provider.generate(system_prompt="s", user_prompt="u")

    assert result.cached_tokens == 16
    assert result.usage["cached_tokens"] == 16


async def test_generate_warns_on_length_truncation():
    """A reasoning-only response (finish_reason=length, empty content) warns."""
    provider = KimiProvider(api_key="sk-kimi-test")
    mock_response = _make_mock_chat_response("", finish_reason="length")

    with (
        patch("openai.AsyncOpenAI") as mock_client,
        patch("repowise.core.providers.llm.kimi.log.warning") as mock_warn,
    ):
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        result = await provider.generate(system_prompt="s", user_prompt="u", max_tokens=16)

    assert result.content == ""
    mock_warn.assert_called_once()
    assert mock_warn.call_args.args[0] == "kimi.generate.truncated"
    assert mock_warn.call_args.kwargs["content_empty"] is True


async def test_generate_no_truncation_warning_on_normal_finish():
    provider = KimiProvider(api_key="sk-kimi-test")
    mock_response = _make_mock_chat_response("done", finish_reason="stop")

    with (
        patch("openai.AsyncOpenAI") as mock_client,
        patch("repowise.core.providers.llm.kimi.log.warning") as mock_warn,
    ):
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        await provider.generate(system_prompt="s", user_prompt="u")

    mock_warn.assert_not_called()
