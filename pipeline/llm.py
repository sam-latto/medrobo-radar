import contextvars
import logging
from typing import Callable

import anthropic
from config import ANTHROPIC_API_KEY, MODEL
from pipeline.pricing import cost_usd

logger = logging.getLogger(__name__)

# Optional receiver for every call's token usage: fn(record: dict). Set it with
# usage_sink.set(fn) around work whose cost should be recorded (e.g. one user's task).
# Threads started inside that work need contextvars.copy_context() to see it.
usage_sink: contextvars.ContextVar[Callable[[dict], None] | None] = contextvars.ContextVar(
    "usage_sink", default=None)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Newer models reject forced tool_choice ("tool"/"any") with a 400; for those we
# fall back to tool_choice "auto" plus an explicit instruction to call the tool.
_NO_FORCED_TOOL_CHOICE = (
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-fable-5-1",
    "claude-mythos-5-1",
)


_ADAPTIVE_THINKING = (
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
)


def _supports_forced_tool_choice(model: str) -> bool:
    return not any(model.startswith(m) for m in _NO_FORCED_TOOL_CHOICE)


def thinking_param() -> dict | None:
    """Adaptive thinking for models that support it; None for older models."""
    return {"type": "adaptive"} if MODEL.startswith(_ADAPTIVE_THINKING) else None


def call_tool(prompt: str, tool: dict, max_tokens: int = 4096, system: str | None = None) -> dict:
    """Make one Claude call that must answer via `tool`; return the tool input dict.

    Raises RuntimeError if the model does not call the tool.
    """
    kwargs = {}
    if system:
        kwargs["system"] = system

    if _supports_forced_tool_choice(MODEL):
        tool_choice = {"type": "tool", "name": tool["name"]}
    else:
        tool_choice = {"type": "auto"}
        prompt = f"{prompt}\n\nRespond only by calling the `{tool['name']}` tool."

    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        tools=[tool],
        tool_choice=tool_choice,
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    _record_usage(tool["name"], response)
    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError(
            f"Model did not call {tool['name']} (stop_reason={response.stop_reason})"
        )
    if response.stop_reason == "max_tokens":
        logger.warning(f"{tool['name']} response hit max_tokens; input may be truncated")
    return tool_use.input


def _record_usage(purpose: str, response) -> None:
    sink = usage_sink.get()
    usage = getattr(response, "usage", None)
    if sink is None or usage is None:
        return
    record = {
        "purpose": purpose,
        "model": MODEL,
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }
    record["cost_usd"] = cost_usd(MODEL, record["input_tokens"], record["output_tokens"],
                                  record["cache_creation_tokens"], record["cache_read_tokens"])
    try:
        sink(record)
    except Exception as e:  # recording cost must never break the call itself
        logger.error(f"Usage sink failed: {e}")
