"""Offline API-equivalent cost estimates; never model calls or account billing."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PRICES = Path(__file__).resolve().parent / "reference-prices.json"


def estimate_cost(result, prices):
    """Preserve CLI estimates; fill Codex's missing dollars using a dated price snapshot."""
    if result.get("cost_usd") is not None:
        return result["cost_usd"], result.get("cost_source", "cli_reported")
    if result.get("cli") != "codex":
        return None, "unreported"
    rate = prices.get("models", {}).get(result.get("model"))
    usage = result.get("usage")
    if not rate or not isinstance(usage, dict):
        return None, "unreported"
    names = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens")
    values = [usage.get(k) for k in names]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in values):
        return None, "unreported"
    inputs, outputs, reads, writes = values
    fresh = inputs - reads - writes
    if fresh < 0:
        return None, "unreported"
    cost = (fresh*rate["input_per_million"] + reads*rate["cached_input_per_million"]
            + writes*rate["cache_write_per_million"] + outputs*rate["output_per_million"]) / 1e6
    return cost, "reference_price_estimate"


def apply_estimate(result, prices):
    value, source = estimate_cost(result, prices)
    result["estimated_cost_usd"] = value
    result["estimated_cost_source"] = source
    return result


def load_prices(path=DEFAULT_PRICES):
    return json.loads(Path(path).read_text())
