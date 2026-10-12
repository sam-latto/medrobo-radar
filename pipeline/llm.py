import contextvars
import logging
from typing import Callable

import anthropic
from config import ANTHROPIC_API_KEY, MODEL
from pipeline.pricing import cost_usd, search_cost_usd

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


def _tool_params(prompt: str, tool: dict, max_tokens: int, system, model: str) -> dict:
    """Messages API parameters for a call that must answer via `tool`."""
    if _supports_forced_tool_choice(model):
        tool_choice = {"type": "tool", "name": tool["name"]}
    else:
        tool_choice = {"type": "auto"}
        prompt = f"{prompt}\n\nRespond only by calling the `{tool['name']}` tool."
    params = {"model": model, "max_tokens": max_tokens, "tools": [tool], "tool_choice": tool_choice,
              "messages": [{"role": "user", "content": prompt}]}
    if system:
        params["system"] = system
    return params


def _tool_input(message, tool: dict) -> dict:
    tool_use = next((b for b in message.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError(f"Model did not call {tool['name']} (stop_reason={message.stop_reason})")
    if message.stop_reason == "max_tokens":
        logger.warning(f"{tool['name']} response hit max_tokens; input may be truncated")
    return tool_use.input


def call_tool(prompt: str, tool: dict, max_tokens: int = 4096, system: str | None = None,
              model: str | None = None) -> dict:
    """Make one Claude call that must answer via `tool`; return the tool input dict.

    `model` defaults to ANTHROPIC_MODEL. Raises RuntimeError if the model does not call the tool.
    """
    model = model or MODEL
    response = client.messages.create(**_tool_params(prompt, tool, max_tokens, system, model))
    _record_usage(tool["name"], response, model)
    return _tool_input(response, tool)


# ── Message Batches (50% off) ────────────────────────────────────────────────
#
# The overnight worker turns batch mode on; call_tools() then sends a group of calls as
# one Message Batch and waits for it. Most batches finish within minutes; if one takes
# longer than BATCH_TIMEOUT_MINUTES, or can't be created, the calls are made directly.

batch_mode: contextvars.ContextVar[bool] = contextvars.ContextVar("batch_mode", default=False)
BATCH_POLL_SECONDS = 20
BATCH_TIMEOUT_MINUTES = 90
BATCH_DISCOUNT = 0.5


def call_tools(requests: list[dict]) -> list:
    """Run several tool calls. Each request has prompt, tool and optionally max_tokens,
    system, model. Returns, in order, each call's tool input or the Exception it raised."""
    if batch_mode.get() and requests:
        try:
            return _call_tools_batched(requests)
        except Exception as e:
            logger.error(f"Message batch failed ({e}); making the calls directly")
    out = []
    for r in requests:
        try:
            out.append(call_tool(r["prompt"], r["tool"], max_tokens=r.get("max_tokens", 4096),
                                 system=r.get("system"), model=r.get("model")))
        except Exception as e:
            out.append(e)
    return out


def _call_tools_batched(requests: list[dict]) -> list:
    import time

    params = [_tool_params(r["prompt"], r["tool"], r.get("max_tokens", 4096), r.get("system"),
                           r.get("model") or MODEL) for r in requests]
    batch = client.messages.batches.create(
        requests=[{"custom_id": f"r{i}", "params": p} for i, p in enumerate(params)])
    logger.info(f"Message batch {batch.id}: {len(requests)} requests submitted")
    deadline = time.monotonic() + BATCH_TIMEOUT_MINUTES * 60
    while batch.processing_status != "ended":
        if time.monotonic() > deadline:
            client.messages.batches.cancel(batch.id)
            raise TimeoutError(f"batch {batch.id} still running after {BATCH_TIMEOUT_MINUTES} minutes")
        time.sleep(BATCH_POLL_SECONDS)
        batch = client.messages.batches.retrieve(batch.id)

    out: list = [RuntimeError("missing from batch results")] * len(requests)
    for result in client.messages.batches.results(batch.id):
        i = int(result.custom_id[1:])
        tool = requests[i]["tool"]
        if result.result.type == "succeeded":
            message = result.result.message
            _record_usage(tool["name"], message, params[i]["model"], discount=BATCH_DISCOUNT)
            try:
                out[i] = _tool_input(message, tool)
            except Exception as e:
                out[i] = e
        else:
            out[i] = RuntimeError(f"batch request {result.result.type}")
    failed = sum(isinstance(o, Exception) for o in out)
    logger.info(f"Message batch {batch.id} done: {len(out) - failed} succeeded, {failed} failed")
    return out


def record_search(purpose: str, depth: str = "basic") -> None:
    """Record one Tavily search's estimated cost with the current usage sink, if any."""
    sink = usage_sink.get()
    if sink is None:
        return
    try:
        sink({"purpose": purpose, "model": f"tavily:{depth}", "input_tokens": 0, "output_tokens": 0,
              "cache_creation_tokens": 0, "cache_read_tokens": 0, "cost_usd": search_cost_usd(depth),
              "kind": "search"})
    except Exception as e:
        logger.error(f"Usage sink failed: {e}")


def _record_usage(purpose: str, response, model: str | None = None, discount: float = 1.0) -> None:
    sink = usage_sink.get()
    usage = getattr(response, "usage", None)
    if sink is None or usage is None:
        return
    model = model or MODEL
    record = {
        "purpose": purpose,
        "model": model,
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }
    record["cost_usd"] = round(discount * cost_usd(model, record["input_tokens"], record["output_tokens"],
                                                  record["cache_creation_tokens"], record["cache_read_tokens"]), 6)
    try:
        sink(record)
    except Exception as e:  # recording cost must never break the call itself
        logger.error(f"Usage sink failed: {e}")
