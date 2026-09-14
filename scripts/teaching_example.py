"""Run the hand-calculated classroom example; no model, GPU or API is needed."""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
from pathlib import Path

from docreranker.evaluation import ranking_metrics
from docreranker.training.rewards import format_reward, result_reward


def worked_example():
    candidates = ["A", "B", "C", "D", "E"]
    gold = {"B", "F"}  # F was missed by retrieval; the reranker never sees it.
    reranked = ["B", "A", "C", "D", "E"]
    before = ranking_metrics(candidates, gold)
    after = ranking_metrics(reranked, gold)
    for actual, expected in ((before["recall@1"], 0), (after["recall@1"], Fraction(1, 2)),
                             (after["hit@1"], 1), (before["mrr"], Fraction(1, 2)),
                             (after["mrr"], 1), (before["recall@5"], Fraction(1, 2)),
                             (after["recall@5"], Fraction(1, 2))):
        assert math.isclose(actual, float(expected), abs_tol=1e-12)
    discount = 1 / math.log2(3)
    assert math.isclose(before["ndcg@3"], discount / (1 + discount), abs_tol=1e-12)
    assert math.isclose(after["ndcg@3"], 1 / (1 + discount), abs_tol=1e-12)

    # A separate training example has positive image positions 2 and 4.
    positive_ids = [2, 4]
    text = "<think>Images 2 and 4 contain the relevant evidence.</think><answer>[2, 1, 4, 3, 5]</answer>"
    cubic = result_reward(text, positive_ids, 5)
    expected_cubic = Fraction(224, 243)  # (1 + 1/3^3) / (1 + 1/2^3)
    assert math.isclose(cubic, float(expected_cubic), abs_tol=1e-12)
    invalid = "<think>Repeated image IDs are invalid.</think><answer>[2, 2, 4, 3, 5]</answer>"
    assert result_reward(invalid, positive_ids, 5) == 0
    assert format_reward(invalid, 5) == 0
    return {"example_type": "synthetic teaching arithmetic, not experimental results",
            "evaluation": {"candidates": candidates, "all_gold": sorted(gold),
                           "missing_gold": ["F"], "reranked": reranked,
                           "before": before, "after": after},
            "separate_training_example": {"positive_image_positions": positive_ids,
                "output": text, "result_reward_fraction": str(expected_cubic),
                "result_reward": cubic, "format_reward": format_reward(text, 5),
                "invalid_output": invalid, "invalid_result_reward": 0, "invalid_format_reward": 0},
            "hand_calculation_checks": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = worked_example()
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
