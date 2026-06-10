"""
Estimate LLM cost from token usage (CrewAI UsageMetrics or prompt/completion counts).

Pricing can be set via env for custom models:
  OAPE_COST_INPUT_PER_1M   - USD per 1M input tokens (default from model table or 0)
  OAPE_COST_OUTPUT_PER_1M  - USD per 1M output tokens
  OAPE_MODEL_NAME          - Model name for display (e.g. claude-3-5-haiku)
"""

import os
from typing import Any, Optional


# Approximate USD per 1M tokens (input, output). Update as needed; override with env.
_DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-opus": (15.00, 75.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
}


def estimate_cost(
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: Optional[int] = None,
    model_name: Optional[str] = None,
) -> dict[str, Any]:
    """
    Estimate cost in USD from token counts.

    Args:
        prompt_tokens: Input/prompt tokens.
        completion_tokens: Output/completion tokens.
        total_tokens: If set and prompt+completion are 0, used for rough estimate (split 70/30).
        model_name: Model identifier for pricing lookup (e.g. claude-3-5-haiku).

    Returns:
        Dict with prompt_tokens, completion_tokens, total_tokens, cost_usd, model (display).
    """
    if total_tokens is not None and prompt_tokens == 0 and completion_tokens == 0:
        prompt_tokens = int(total_tokens * 0.7)
        completion_tokens = total_tokens - prompt_tokens
    total = prompt_tokens + completion_tokens

    model = (model_name or os.getenv("OAPE_MODEL_NAME", "").strip()).lower() or None
    input_per_1m = None
    output_per_1m = None
    try:
        input_per_1m = float(os.getenv("OAPE_COST_INPUT_PER_1M", "").strip())
    except (ValueError, AttributeError):
        pass
    try:
        output_per_1m = float(os.getenv("OAPE_COST_OUTPUT_PER_1M", "").strip())
    except (ValueError, AttributeError):
        pass

    if input_per_1m is None or output_per_1m is None:
        # Prefer longest matching key so "claude-3-5-sonnet" matches before "claude-3-5" if both existed
        best_key = None
        if model:
            for key in sorted(_DEFAULT_PRICES.keys(), key=len, reverse=True):
                if key in model:
                    best_key = key
                    break
        if best_key is not None:
            in_p, out_p = _DEFAULT_PRICES[best_key]
            input_per_1m = input_per_1m if input_per_1m is not None else in_p
            output_per_1m = output_per_1m if output_per_1m is not None else out_p
        if input_per_1m is None:
            input_per_1m = 1.0  # fallback guess
        if output_per_1m is None:
            output_per_1m = 4.0

    cost_usd = (prompt_tokens / 1_000_000 * input_per_1m) + (
        completion_tokens / 1_000_000 * output_per_1m
    )

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total,
        "cost_usd": round(cost_usd, 4),
        "model": model_name or model or "unknown",
    }


def estimate_cost_from_usage_metrics(usage: Any) -> Optional[dict[str, Any]]:
    """
    Build cost estimate from CrewAI UsageMetrics (or object with prompt_tokens, completion_tokens, total_tokens).
    """
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0)
    completion = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0)
    total = getattr(usage, "total_tokens", None)
    if isinstance(prompt, (int, float)) and isinstance(completion, (int, float)):
        prompt, completion = int(prompt), int(completion)
    else:
        return None
    return estimate_cost(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=int(total) if total is not None else None,
    )
