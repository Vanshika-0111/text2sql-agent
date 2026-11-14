"""Collects token usage from every Gemini response during one question."""

CALLS = []


def reset():
    CALLS.clear()


def record_usage(label, response):
    u = getattr(response, "usage_metadata", None)
    CALLS.append({
        "call": label,
        "prompt_tokens": getattr(u, "prompt_token_count", 0) or 0,
        "output_tokens": getattr(u, "candidates_token_count", 0) or 0,
        "thinking_tokens": getattr(u, "thoughts_token_count", 0) or 0,
        "total_tokens": getattr(u, "total_token_count", 0) or 0,
    })


def summary():
    return {
        "gemini_calls": len(CALLS),
        "prompt_tokens": sum(c["prompt_tokens"] for c in CALLS),
        "output_tokens": sum(c["output_tokens"] for c in CALLS),
        "thinking_tokens": sum(c["thinking_tokens"] for c in CALLS),
        "total_tokens": sum(c["total_tokens"] for c in CALLS),
        "calls": list(CALLS),
    }