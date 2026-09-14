"""Training data validation, image mapping and resolution-balanced resampling."""

from __future__ import annotations

import copy
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any

from .rewards import parse_completion, validate_labels


def multimodal_messages(messages: list[dict[str, str]], candidate_count: int) -> list[dict[str, Any]]:
    """Replace <image> in encounter order; otherwise append numbered image slots.

    All contents become structured lists, so Arrow can store the messages and
    TRL fills image slots instead of silently inserting extra placeholders.
    """
    placeholders = sum(m["content"].count("<image>") for m in messages)
    if placeholders not in (0, candidate_count):
        raise ValueError(f"expected zero or {candidate_count} <image> placeholders, got {placeholders}")
    result = []
    inserted = False
    for message in messages:
        if "<image>" in message["content"] and message["role"] != "user":
            raise ValueError("image placeholders are only supported in user messages")
        parts = message["content"].split("<image>")
        content: list[dict[str, str]] = []
        for index, part in enumerate(parts):
            if part:
                content.append({"type": "text", "text": part})
            if index < len(parts) - 1:
                content.append({"type": "image"})
        if not placeholders and message["role"] == "user" and not inserted:
            for index in range(1, candidate_count + 1):
                content.extend([{"type": "text", "text": f"\nImage {index}: "}, {"type": "image"}])
            inserted = True
        result.append({"role": message["role"], "content": content})
    if not any(m["role"] == "user" for m in result):
        raise ValueError("training prompt must contain a user message")
    return result


def load_rows(path: str | Path, stage: str, excluded_queries: str | Path | None = None) -> list[dict[str, Any]]:
    """Read the shared JSONL format and validate everything before model loading."""
    from PIL import Image

    if stage not in {"sft", "grpo"}:
        raise ValueError("stage must be sft or grpo")
    excluded = set()
    if excluded_queries:
        with Path(excluded_queries).open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    item = json.loads(line)
                    excluded.add(str(item["query_id"]))
    seen: set[str] = set()
    dimensions: dict[str, int] = {}
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("row must be a JSON object")
                if str(row.get("split", "")).lower() in {"evaluation", "eval", "test", "validation"}:
                    raise ValueError("evaluation/test/validation split cannot be used for training")
                query_id = row.get("query_id")
                if not isinstance(query_id, str) or not query_id.strip():
                    raise ValueError("query_id must be a nonempty string")
                if query_id in seen:
                    raise ValueError(f"duplicate query_id: {query_id}")
                if query_id in excluded:
                    raise ValueError(f"evaluation query leaked into training: {query_id}")
                images = row.get("images")
                if not isinstance(images, list) or not images:
                    raise ValueError("images must be a nonempty list of absolute local paths")
                resolved = []
                for image in images:
                    if not isinstance(image, str) or not Path(image).is_absolute():
                        raise ValueError("each image must have an absolute local path")
                    image_path = str(Path(image).resolve(strict=True))
                    if image_path not in dimensions:
                        with Image.open(image_path) as opened:
                            dimensions[image_path] = opened.width * opened.height
                            opened.verify()
                    resolved.append(image_path)
                if len(set(resolved)) != len(resolved):
                    raise ValueError("candidate images must not repeat the same file")
                positive_ids = row.get("positive_ids")
                if not isinstance(positive_ids, list):
                    raise ValueError("positive_ids must be a list")
                validate_labels(positive_ids, len(images))
                if stage == "sft":
                    messages = row.get("messages")
                    if not isinstance(messages, list) or len(messages) < 2:
                        raise ValueError("SFT messages must include a user prompt and assistant answer")
                    for message in messages:
                        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"}:
                            raise ValueError("messages have an unsupported role")
                        if not isinstance(message.get("content"), str) or not message["content"].strip():
                            raise ValueError("message content must be a nonempty string")
                    if messages[-1]["role"] != "assistant" or messages[-2]["role"] != "user":
                        raise ValueError("SFT messages must end with a user prompt then assistant answer")
                    target_order = parse_completion(messages[-1]["content"], len(images))
                    if target_order is None:
                        raise ValueError("SFT answer must contain rationale and a complete valid candidate permutation")
                    if set(target_order[:len(positive_ids)]) != set(positive_ids):
                        raise ValueError("SFT target must rank every positive candidate before all negatives")
                    multimodal_messages(messages[:-1], len(images))
                elif not isinstance(row.get("query"), str) or not row["query"].strip():
                    raise ValueError("GRPO query must be a nonempty string")
                row = copy.deepcopy(row)
                row["images"] = resolved
                row["candidate_count"] = len(images)
                row["mean_pixels"] = sum(dimensions[p] for p in resolved) / len(resolved)
                seen.add(query_id)
                rows.append(row)
            except (ValueError, KeyError, TypeError, OSError) as error:
                raise ValueError(f"{path}:{line_number}: {error}") from error
    if not rows:
        raise ValueError(f"training data is empty: {path}")
    return rows


def resolution_balanced_indices(rows: list[dict[str, Any]], bins: int = 10, seed: int = 42,
                                epoch_size: int | None = None) -> tuple[list[int], dict[str, Any]]:
    """Equal allocation across nonempty equal-width log-pixel bins.

    This is not quantile binning: dense resolution ranges are undersampled and
    sparse ranges are oversampled. Sampling is fixed for a run; the trainer may
    shuffle the resulting dataset each epoch. GRPO retains its own RepeatSampler.
    """
    if not rows or type(bins) is not int or bins < 1:
        raise ValueError("nonempty rows and a positive integer bin count are required")
    if epoch_size is not None and (type(epoch_size) is not int or epoch_size < 1):
        raise ValueError("epoch_size must be a positive integer")
    values = [math.log2(row["mean_pixels"]) for row in rows]
    lower, upper = min(values), max(values)
    groups: dict[int, list[int]] = {}
    for index, value in enumerate(values):
        bucket = 0 if upper == lower else min(bins - 1, int((value - lower) / (upper - lower) * bins))
        groups.setdefault(bucket, []).append(index)
    rng = random.Random(seed)
    per_bin = math.ceil((epoch_size or len(rows)) / len(groups))
    selected = []
    for indices in groups.values():
        shuffled = list(indices)
        rng.shuffle(shuffled)
        selected.extend(shuffled[:per_bin])
        if len(shuffled) < per_bin:
            selected.extend(rng.choices(shuffled, k=per_bin - len(shuffled)))
    rng.shuffle(selected)
    report = {
        "method": "equal_width_log2_mean_candidate_pixels",
        "bins": bins,
        "nonempty_bins": len(groups),
        "log2_min_pixels": lower,
        "log2_max_pixels": upper,
        "source_bin_counts": {str(k): len(v) for k, v in sorted(groups.items())},
        "sampled_per_nonempty_bin": per_bin,
        "source_examples": len(rows),
        "sampled_examples": len(selected),
        "distinct_sampled_examples": len(Counter(selected)),
        "seed": seed,
    }
    return selected, report


def make_dataset(rows: list[dict[str, Any]], indices: list[int], stage: str):
    """Load optional datasets only for actual training; images decode on demand."""
    from datasets import Dataset, Image, Sequence

    from docreranker.prompts import SYSTEM_PROMPT, ranking_prompt

    prepared = []
    for row in rows:
        item = {key: row[key] for key in ("query_id", "images", "positive_ids", "candidate_count")}
        if stage == "sft":
            item["prompt"] = multimodal_messages(row["messages"][:-1], row["candidate_count"])
            item["completion"] = [row["messages"][-1]]
        else:
            item["prompt"] = multimodal_messages([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": ranking_prompt(row["query"], row["candidate_count"])},
            ], row["candidate_count"])
        prepared.append(item)
    dataset = Dataset.from_list(prepared).cast_column("images", Sequence(Image()))
    return dataset.select(indices)
