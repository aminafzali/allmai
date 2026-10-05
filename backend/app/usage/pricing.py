"""Static per-model USD estimates for the usage ledger.

Values are USD per 1K tokens (prompt, completion), best-effort public
figures — update them when providers change pricing. Unknown
provider/model pairs estimate to None (recorded, never billed):
a missing price must read as unknown, never as zero-cost.
"""

# (provider, model) -> (usd_per_1k_prompt, usd_per_1k_completion)
_PRICES_USD_PER_1K: dict[tuple[str, str], tuple[float, float]] = {
    ("openai_compat", "gpt-4o-mini"): (0.00015, 0.0006),
    ("openai_compat", "gpt-4.1-nano"): (0.0001, 0.0004),
    ("openai_compat", "text-embedding-3-small"): (0.00002, 0.0),
    ("openai_compat", "gemini-2.5-flash"): (0.0003, 0.0025),
    ("openai_compat", "gemini-2.5-flash-lite"): (0.0001, 0.0004),
    ("openai_compat", "gemini-2.0-flash"): (0.0001, 0.0004),
    ("gemini", "gemini-2.0-flash"): (0.0001, 0.0004),
    ("gemini", "gemini-2.5-flash"): (0.0003, 0.0025),
}


def estimate_cost_usd(provider: str | None, model: str | None,
                      prompt_tokens: int = 0,
                      completion_tokens: int = 0) -> float | None:
    """Estimated USD for one measured call, or None when unpriced."""
    if not model:
        return None
    key = ((provider or "").strip().lower(), str(model).strip())
    price = _PRICES_USD_PER_1K.get(key)
    if price is None:
        # Fall back to any-provider match on the model id (the same
        # model is often reachable through several gateways).
        for (prov, name), value in _PRICES_USD_PER_1K.items():
            if name == key[1]:
                price = value
                break
    if price is None:
        return None
    try:
        cost = (max(0, int(prompt_tokens or 0)) * price[0]
                + max(0, int(completion_tokens or 0)) * price[1]) / 1000.0
    except (TypeError, ValueError):
        return None
    return round(cost, 6)
