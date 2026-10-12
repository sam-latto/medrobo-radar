"""Claude API prices and per-call cost.

Prices are USD per million tokens, Anthropic first-party rates (October 2026).
Cache writes (5-minute TTL) cost 1.25x the input price; cache reads use the listed
price, or 0.1x the input price where none is listed.
"""
import logging
import os

logger = logging.getLogger(__name__)

# model: (input, output, cache_read or None)
PRICES_PER_MTOK: dict[str, tuple[float, float, float | None]] = {
    "claude-fable-5-1": (10.00, 50.00, 0.25),
    "claude-fable-5": (10.00, 50.00, None),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, None),
    "claude-opus-4-8": (5.00, 25.00, None),
    "claude-opus-4-7": (5.00, 25.00, None),
    "claude-opus-4-6": (5.00, 25.00, None),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, None),
    "claude-sonnet-4-6": (3.00, 15.00, None),
    "claude-haiku-5-5": (0.10, 0.50, None),
    "claude-haiku-4-5": (1.00, 5.00, None),
}
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.1

# Tavily bills in credits: a basic search is 1 credit, an advanced search 2.
# Pay-as-you-go price per credit; set TAVILY_USD_PER_CREDIT to match your plan.
TAVILY_USD_PER_CREDIT = float(os.getenv("TAVILY_USD_PER_CREDIT", "0.008"))
TAVILY_CREDITS = {"basic": 1, "advanced": 2}

_warned: set[str] = set()


def search_cost_usd(depth: str = "basic") -> float:
    return round(TAVILY_CREDITS.get(depth, 1) * TAVILY_USD_PER_CREDIT, 6)


def cost_usd(model: str, input_tokens: int = 0, output_tokens: int = 0,
             cache_creation_tokens: int = 0, cache_read_tokens: int = 0) -> float:
    """Dollar cost of one call. Unknown models cost 0 (and log a warning once)."""
    prices = PRICES_PER_MTOK.get(model)
    if prices is None:
        if model not in _warned:
            _warned.add(model)
            logger.warning(f"No price for model {model}; recording cost as 0")
        return 0.0
    inp, out, cache_read = prices
    if cache_read is None:
        cache_read = inp * CACHE_READ_MULTIPLIER
    total = ((input_tokens or 0) * inp
             + (output_tokens or 0) * out
             + (cache_creation_tokens or 0) * inp * CACHE_WRITE_MULTIPLIER
             + (cache_read_tokens or 0) * cache_read)
    return round(total / 1_000_000, 6)
