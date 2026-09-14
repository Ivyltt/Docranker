"""Strict, deterministic ranking rewards with no model dependencies.

The PDF's 1/rank**3 reward is preserved. A completion must contain exactly one
complete permutation before *either* reward is paid. This deliberately tightens
the PDF's soft format checks: omitted, repeated or invented IDs cannot collect
format credit or a deceptively high result reward.
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

_STRUCTURE = re.compile(
    r"\s*<think>(?P<rationale>.*?)</think>\s*<answer>\s*(?P<answer>\[[^\[\]]*\])\s*</answer>\s*",
    re.DOTALL,
)


def completion_text(completion: Any) -> str:
    """Accept both TRL plain strings and one assistant conversational message."""
    if isinstance(completion, str):
        return completion
    if isinstance(completion, list) and len(completion) == 1:
        message = completion[0]
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str):
                return content
    return ""


def validate_labels(positive_ids: Sequence[int], candidate_count: int) -> None:
    if type(candidate_count) is not int or candidate_count < 1:
        raise ValueError("candidate_count must be a positive integer")
    if not positive_ids:
        raise ValueError("a training example must have at least one positive candidate")
    if any(type(i) is not int or not 1 <= i <= candidate_count for i in positive_ids):
        raise ValueError("positive_ids must be 1-based integer candidate IDs")
    if len(set(positive_ids)) != len(positive_ids):
        raise ValueError("positive_ids must not contain duplicates")


def parse_completion(completion: Any, candidate_count: int) -> list[int] | None:
    """Return a complete candidate permutation, or None for any invalid output."""
    if type(candidate_count) is not int or candidate_count < 1:
        raise ValueError("candidate_count must be a positive integer")
    text = completion_text(completion)
    match = _STRUCTURE.fullmatch(text)
    if match is None or not match["rationale"].strip():
        return None
    # Nested/repeated protocol tags can otherwise hide another answer in think.
    if any(tag in match["rationale"] for tag in ("<think>", "</think>", "<answer>", "</answer>")):
        return None
    try:
        order = json.loads(match["answer"])
    except (json.JSONDecodeError, ValueError):
        return None
    if len(order) != candidate_count or any(type(i) is not int for i in order):
        return None
    if set(order) != set(range(1, candidate_count + 1)):
        return None
    return order


def result_reward(completion: Any, positive_ids: Sequence[int], candidate_count: int) -> float:
    """Normalized discounted relevant-page gain, with cubic rank discount."""
    validate_labels(positive_ids, candidate_count)
    order = parse_completion(completion, candidate_count)
    if order is None:
        return 0.0
    positive = set(positive_ids)
    actual = sum(1.0 / rank**3 for rank, page in enumerate(order, 1) if page in positive)
    ideal = sum(1.0 / rank**3 for rank in range(1, len(positive) + 1))
    return min(1.0, max(0.0, actual / ideal))


def format_reward(completion: Any, candidate_count: int) -> float:
    """Full structure and permutation validity; invalid candidates earn zero."""
    return float(parse_completion(completion, candidate_count) is not None)


def rerank_reward(completions: list[Any], positive_ids: list[list[int]],
                  candidate_count: list[int], **kwargs: Any) -> list[float]:
    """TRL reward function; extra dataset columns arrive through kwargs."""
    if not len(completions) == len(positive_ids) == len(candidate_count):
        raise ValueError("reward batch columns have different lengths")
    return [result_reward(c, p, n) for c, p, n in zip(completions, positive_ids, candidate_count)]


def format_rerank_reward(completions: list[Any], candidate_count: list[int],
                         **kwargs: Any) -> list[float]:
    if len(completions) != len(candidate_count):
        raise ValueError("reward batch columns have different lengths")
    return [format_reward(c, n) for c, n in zip(completions, candidate_count)]
