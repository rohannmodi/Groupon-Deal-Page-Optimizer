"""
Anthropic API client wrapper.

Responsibilities:
  - Single place to configure the SDK (api key, default model, timeouts)
  - tool_use helper: send a prompt with a tool schema, extract the tool call result
  - Retry logic via tenacity (handles rate limits + transient errors)
  - Token usage logging (written to DB by callers)

All AI calls in this pipeline go through `call_with_tool()` or `call_text()`.
Extended thinking is enabled per-call via the `thinking_budget` parameter.

Both sync and async variants are provided:
  - call_with_tool() / call_text()            → sync (for simple scripts)
  - async_call_with_tool() / async_call_text() → async (for pipeline stages)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import anthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings

log = logging.getLogger(__name__)

# Default model — claude-sonnet-4-5 is the best balance of speed/quality/cost
# for structured output tasks with long research context windows.
DEFAULT_MODEL = "claude-sonnet-4-5"
LONG_CONTEXT_MODEL = "claude-sonnet-4-5"  # switch to opus for final proposal if budget allows


class TokenUsage:
    """Tracks prompt + completion tokens for cost reporting."""

    def __init__(self, prompt: int = 0, completion: int = 0):
        self.prompt = prompt
        self.completion = completion

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    @property
    def estimated_cost_usd(self) -> float:
        """Rough estimate using Sonnet pricing."""
        return (self.prompt * 3.00 + self.completion * 15.00) / 1_000_000

    def __repr__(self) -> str:
        return f"TokenUsage(prompt={self.prompt}, completion={self.completion}, cost≈${self.estimated_cost_usd:.4f})"


class ToolCallResult:
    """The parsed result of a tool_use AI call."""

    def __init__(self, data: dict[str, Any], usage: TokenUsage):
        self.data = data    # the raw dict from the tool input
        self.usage = usage


# ---------------------------------------------------------------------------
# Client singletons (lazy)
# ---------------------------------------------------------------------------

_sync_client: Optional[anthropic.Anthropic] = None
_async_client: Optional[anthropic.AsyncAnthropic] = None


def get_client() -> anthropic.Anthropic:
    global _sync_client
    if _sync_client is None:
        _sync_client = _make_sync_client()
    return _sync_client


def get_async_client() -> anthropic.AsyncAnthropic:
    global _async_client
    if _async_client is None:
        _async_client = _make_async_client()
    return _async_client


def _make_sync_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file."
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _make_async_client() -> anthropic.AsyncAnthropic:
    if not settings.anthropic_api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file."
        )
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(4),
    reraise=True,
)
def call_with_tool(
    messages: list[dict],
    tool_name: str,
    tool_schema: dict,
    *,
    system: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    thinking_budget: Optional[int] = None,
) -> ToolCallResult:
    """
    Send messages to Claude with a single tool definition and force it to
    call that tool (tool_choice={"type": "tool", "name": tool_name}).

    Returns a ToolCallResult with the parsed tool input dict.

    Raises ValueError if Claude doesn't call the expected tool.
    """
    client = get_client()
    kwargs = _build_kwargs(
        messages, tool_name, tool_schema,
        system=system, model=model, max_tokens=max_tokens,
        thinking_budget=thinking_budget,
    )

    log.debug("Calling Claude (%s) with tool=%s", model, tool_name)
    response = _create_message_sync(client, kwargs)
    return _parse_tool_response(response, tool_name)


@retry(
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(4),
    reraise=True,
)
def call_text(
    messages: list[dict],
    *,
    system: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2048,
) -> tuple[str, TokenUsage]:
    """Simple text completion (no tools). Returns (text, usage)."""
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    usage = TokenUsage(
        prompt=response.usage.input_tokens,
        completion=response.usage.output_tokens,
    )
    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    return text, usage


# ---------------------------------------------------------------------------
# Async API  (used by the asyncio pipeline stages)
# ---------------------------------------------------------------------------

async def async_call_with_tool(
    messages: list[dict],
    tool_name: str,
    tool_schema: dict,
    *,
    system: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    thinking_budget: Optional[int] = None,
) -> ToolCallResult:
    """
    Async version of call_with_tool. Uses AsyncAnthropic client.
    Retries handled via asyncio.sleep inside the loop.
    """
    kwargs = _build_kwargs(
        messages, tool_name, tool_schema,
        system=system, model=model, max_tokens=max_tokens,
        thinking_budget=thinking_budget,
    )

    client = get_async_client()
    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(1, 5):
        try:
            log.debug("Async Claude (%s) tool=%s attempt=%d", model, tool_name, attempt)
            response = await _create_message_async(client, kwargs)
            return _parse_tool_response(response, tool_name)
        except (anthropic.RateLimitError, anthropic.APIStatusError) as exc:
            last_exc = exc
            wait = min(4 * (2 ** (attempt - 1)), 60)
            log.warning("Anthropic rate limit/error, retrying in %ds... (%s)", wait, exc)
            await asyncio.sleep(wait)

    raise last_exc


async def async_call_text(
    messages: list[dict],
    *,
    system: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2048,
) -> tuple[str, TokenUsage]:
    """Async text completion. Returns (text, usage)."""
    client = get_async_client()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system

    response = await client.messages.create(**kwargs)
    usage = TokenUsage(
        prompt=response.usage.input_tokens,
        completion=response.usage.output_tokens,
    )
    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    return text, usage


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_kwargs(
    messages: list[dict],
    tool_name: str,
    tool_schema: dict,
    *,
    system: str,
    model: str,
    max_tokens: int,
    thinking_budget: Optional[int],
) -> dict[str, Any]:
    tools = [{
        "name": tool_name,
        "description": tool_schema.get("description", ""),
        "input_schema": tool_schema["input_schema"],
    }]

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "tools": tools,
        "tool_choice": {"type": "tool", "name": tool_name},
        "messages": messages,
    }

    if system:
        kwargs["system"] = system

    if thinking_budget is not None and "haiku" not in model:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
        # The Anthropic API counts thinking tokens against max_tokens. The
        # caller's `max_tokens` is the budget for the *visible* output (the tool
        # call), so the request ceiling must be thinking_budget + max_tokens.
        # The previous `max(max_tokens, thinking_budget + 2048)` left only ~2048
        # tokens for output after a 16k thinking budget, which truncated the
        # response mid-tool-call and dropped the (last-generated, largest)
        # recommendations array — producing an empty Priority-Ranked section.
        kwargs["max_tokens"] = thinking_budget + max_tokens
        # Anthropic API: tool_choice "tool" (forced) is incompatible with
        # extended thinking. Switch to "auto" — Claude will still call the
        # tool reliably when the prompt and schema are well-specified.
        kwargs["tool_choice"] = {"type": "auto"}

    return kwargs


# The Anthropic SDK refuses a non-streaming request whose max_tokens implies a
# completion that could exceed the 10-minute non-streaming ceiling. Extended
# thinking (which adds its budget on top of the output budget) pushes max_tokens
# past that line, so those requests must be streamed.
_NONSTREAMING_MAX_TOKENS = 8192


def _needs_streaming(kwargs: dict[str, Any]) -> bool:
    return "thinking" in kwargs or kwargs.get("max_tokens", 0) > _NONSTREAMING_MAX_TOKENS


def _create_message_sync(
    client: anthropic.Anthropic, kwargs: dict[str, Any]
) -> anthropic.types.Message:
    if _needs_streaming(kwargs):
        with client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()
    return client.messages.create(**kwargs)


async def _create_message_async(
    client: anthropic.AsyncAnthropic, kwargs: dict[str, Any]
) -> anthropic.types.Message:
    if _needs_streaming(kwargs):
        async with client.messages.stream(**kwargs) as stream:
            return await stream.get_final_message()
    return await client.messages.create(**kwargs)


def _parse_tool_response(
    response: anthropic.types.Message,
    tool_name: str,
) -> ToolCallResult:
    usage = TokenUsage(
        prompt=response.usage.input_tokens,
        completion=response.usage.output_tokens,
    )
    log.info("Claude usage: %s", usage)

    # A max_tokens stop while emitting a tool call means the tool input JSON was
    # cut off mid-stream — fields generated last (e.g. the recommendations
    # array) may be missing or empty. Surface this loudly so callers don't
    # silently ship a half-populated result.
    if response.stop_reason == "max_tokens":
        log.warning(
            "Claude response for tool '%s' hit max_tokens — the tool input may be "
            "truncated and trailing fields (e.g. recommendations) may be missing. "
            "Consider raising max_tokens.",
            tool_name,
        )

    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return ToolCallResult(data=block.input, usage=usage)

    raise ValueError(
        f"Claude did not call tool '{tool_name}'. "
        f"Stop reason: {response.stop_reason}. "
        f"Content types: {[b.type for b in response.content]}"
    )
