"""Optional PDF/reference sampling: resolution strata, no repeated training questions."""
from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Any


def quantile_without_replacement_indices(
    rows: list[dict[str, Any]], bins: int = 10, seed: int = 42,
    epoch_size: int | None = None,
) -> tuple[list[int], dict[str, Any]]:
    """Sort mean image pixels, split equal-count strata, sample each without replacement.

    For 7,200 inputs / 10 groups / 3,000 selected this is the reference's exact
    720-per-group, 300-per-group procedure. Equal-population strata preserve the
    population's approximate resolution proportions; they do not flatten the
    histogram of pixel values. Sorting ties retains the original query order.
    """
    if not rows or type(bins) is not int or bins < 1:
        raise ValueError("nonempty rows and a positive integer bin count are required")
    count = len(rows) if epoch_size is None else epoch_size
    if type(count) is not int or count < 1 or count > len(rows):
        raise ValueError("without-replacement epoch_size must be positive and cannot exceed the source population")
    if type(seed) is not int:
        raise ValueError("sampling seed must be an integer")
    query_ids = [row.get("query_id") for row in rows]
    if (any(not isinstance(query_id, str) or not query_id.strip() for query_id in query_ids)
            or len(set(query_ids)) != len(query_ids)):
        raise ValueError("without-replacement sampling requires unique, nonempty training query IDs")
    if any(type(row.get("mean_pixels")) not in {int, float} or not math.isfinite(row["mean_pixels"])
           or row["mean_pixels"] <= 0 for row in rows):
        raise ValueError("all sampling resolutions must be finite positive mean pixel counts")
    group_count = min(bins, len(rows))
    order = sorted(range(len(rows)), key=lambda index: rows[index]["mean_pixels"])
    width = len(rows) // group_count
    groups = [order[group * width:(group + 1) * width if group < group_count - 1 else len(rows)]
              for group in range(group_count)]
    quotas = [count // group_count] * group_count
    remainder = count - sum(quotas)
    # The reference assigns rounding remainder to the last (largest) group.
    # For tiny populations where it cannot hold that remainder, fill the other
    # strata in order instead of silently returning too few or repeating rows.
    for group in [group_count - 1, *range(group_count - 1)]:
        extra = min(remainder, len(groups[group]) - quotas[group])
        quotas[group] += extra
        remainder -= extra
    if remainder:
        raise ValueError("cannot allocate the requested distinct sample count across strata")
    rng = random.Random(seed)
    selected = []
    for group, quota in zip(groups, quotas, strict=True):
        shuffled = list(group)
        rng.shuffle(shuffled)
        selected.extend(shuffled[:quota])
    chosen = [query_ids[index] for index in selected]
    report = {
        "method": "quantile_without_replacement", "bins": bins, "nonempty_bins": group_count,
        "source_examples": len(rows), "sampled_examples": len(selected),
        "distinct_sampled_examples": len(set(chosen)), "seed": seed,
        "source_bin_counts": {str(index): len(group) for index, group in enumerate(groups)},
        "sampled_bin_counts": {str(index): quota for index, quota in enumerate(quotas)},
        "mean_pixel_ranges": {str(index): [rows[group[0]]["mean_pixels"], rows[group[-1]]["mean_pixels"]]
                              for index, group in enumerate(groups)},
        "selected_query_ids_sha256": hashlib.sha256(json.dumps(chosen, ensure_ascii=False,
                                                               separators=(",", ":")).encode()).hexdigest(),
        "replacement": False, "tie_break": "original input order",
        "interpretation": "Equal-population resolution strata; approximately preserves source resolution proportions, not equal pixel-range mass.",
    }
    return selected, report
