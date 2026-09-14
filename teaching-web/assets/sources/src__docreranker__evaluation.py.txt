"""Evaluate baseline and reranker on identical queries and complete gold labels."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Sequence

from docreranker.inference import candidate_page_ids, input_fingerprint, prediction_from_response, read_jsonl


def ranking_metrics(ranking: Sequence[str], gold: set[str], cutoffs: Sequence[int] = (1, 3, 5)) -> dict[str, float]:
    """Query-level binary relevance metrics, with ALL gold pages in denominators."""
    if not gold:
        raise ValueError("metrics are undefined for a query without gold pages")
    if len(set(ranking)) != len(ranking):
        raise ValueError("ranking contains duplicate page IDs")
    if any(type(k) is not int or k < 1 for k in cutoffs):
        raise ValueError("cutoffs must be positive integers")
    relevance = [int(page_id in gold) for page_id in ranking]
    reciprocal_rank = next((1.0 / rank for rank, hit in enumerate(relevance, 1) if hit), 0.0)
    metrics = {"mrr": reciprocal_rank}
    for k in cutoffs:
        hits = sum(relevance[:k])
        metrics[f"recall@{k}"] = hits / len(gold)
        metrics[f"hit@{k}"] = float(hits > 0)
        dcg = sum(hit / math.log2(rank + 1) for rank, hit in enumerate(relevance[:k], 1))
        ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(k, len(gold)) + 1))
        metrics[f"ndcg@{k}"] = dcg / ideal
    return metrics


def _indexed(records: Sequence[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"{label}: expected objects")
        query_id = record.get("query_id")
        if not isinstance(query_id, str) or not query_id:
            raise ValueError(f"{label}: query_id must be a nonempty string")
        if query_id in indexed:
            raise ValueError(f"{label}: duplicate query_id {query_id!r}")
        indexed[query_id] = record
    return indexed


def validate_predictions(
    records: Sequence[dict[str, Any]], predictions: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Reject missing queries, changed candidates, or stale predictions before scoring."""
    inputs = _indexed(records, "input")
    indexed = _indexed(predictions, "predictions")
    missing = set(inputs) - set(indexed)
    extra = set(indexed) - set(inputs)
    if missing or extra:
        raise ValueError(f"prediction query IDs do not match input; missing={sorted(missing)}, extra={sorted(extra)}")
    for query_id, record in inputs.items():
        baseline = candidate_page_ids(record)
        prediction = indexed[query_id]
        ranked = prediction.get("ranked_page_ids")
        if (not isinstance(ranked, list) or any(not isinstance(page, str) for page in ranked)
                or len(ranked) != len(baseline) or len(set(ranked)) != len(ranked)
                or set(ranked) != set(baseline)):
            raise ValueError(f"{query_id}: ranked_page_ids must be a permutation of the candidate IDs")
        if "baseline_page_ids" in prediction and prediction["baseline_page_ids"] != baseline:
            raise ValueError(f"{query_id}: prediction baseline differs from input candidate order")
        if "input_fingerprint" in prediction and prediction["input_fingerprint"] != input_fingerprint(record):
            raise ValueError(f"{query_id}: prediction fingerprint differs from input")
        if type(prediction.get("fallback")) is not bool:
            raise ValueError(f"{query_id}: fallback must explicitly be a boolean")
        if prediction.get("fallback") and ranked != baseline:
            raise ValueError(f"{query_id}: a fallback prediction must preserve baseline order")
        if "raw_response" in prediction:
            parsed = prediction_from_response(record, prediction["raw_response"])
            if (prediction["fallback"] != parsed["fallback"]
                    or ranked != parsed["ranked_page_ids"]):
                raise ValueError(f"{query_id}: prediction disagrees with its raw_response")
    return indexed


def _aggregate(rows: Sequence[dict[str, Any]], key: str, cutoffs: Sequence[int]) -> dict[str, float | None]:
    names = ["mrr"]
    for k in cutoffs:
        names.extend([f"macro_recall@{k}", f"micro_recall@{k}", f"hit@{k}", f"ndcg@{k}"])
    if not rows:
        return {name: None for name in names}
    n = len(rows)
    total_gold = sum(row["gold_count"] for row in rows)
    result = {"mrr": sum(row[key]["mrr"] for row in rows) / n}
    for k in cutoffs:
        result[f"macro_recall@{k}"] = sum(row[key][f"recall@{k}"] for row in rows) / n
        result[f"micro_recall@{k}"] = sum(
            row[key][f"recall@{k}"] * row["gold_count"] for row in rows
        ) / total_gold
        for metric in ("hit", "ndcg"):
            result[f"{metric}@{k}"] = sum(row[key][f"{metric}@{k}"] for row in rows) / n
    return result


def paired_bootstrap(
    rows: Sequence[dict[str, Any]], cutoffs: Sequence[int], *, samples: int, seed: int = 42,
    confidence: float = 0.95,
) -> dict[str, dict[str, float | None]]:
    """Percentile CI of reranker-minus-baseline; resample paired queries, not pages."""
    if samples < 1 or not 0 < confidence < 1:
        raise ValueError("bootstrap samples must be positive and confidence must be in (0,1)")
    if not rows:
        return {}
    import numpy as np
    rng = np.random.default_rng(seed)
    names = list(_aggregate(rows, "baseline", cutoffs))
    deltas: dict[str, list[float]] = {name: [] for name in names}
    gold_counts = np.asarray([row["gold_count"] for row in rows], dtype=np.float64)
    query_deltas = {}
    for name in names:
        query_name = name.removeprefix("macro_").removeprefix("micro_")
        values = np.asarray([row["reranker"][query_name] - row["baseline"][query_name]
                             for row in rows], dtype=np.float64)
        query_deltas[name] = values * gold_counts if name.startswith("micro_") else values
    for _ in range(samples):
        indices = rng.integers(0, len(rows), size=len(rows))
        sampled_gold = gold_counts[indices].sum()
        for name in names:
            sampled_delta = query_deltas[name][indices]
            delta = sampled_delta.sum() / sampled_gold if name.startswith("micro_") else sampled_delta.mean()
            deltas[name].append(float(delta))
    tail = (1.0 - confidence) / 2
    return {name: {"low": float(np.quantile(values, tail)), "high": float(np.quantile(values, 1 - tail))}
            for name, values in deltas.items()}


def _comparison(rows: Sequence[dict[str, Any]], cutoffs: Sequence[int]) -> dict[str, Any]:
    baseline = _aggregate(rows, "baseline", cutoffs)
    reranker = _aggregate(rows, "reranker", cutoffs)
    return {
        "evaluated_queries": len(rows), "total_gold_pages": sum(row["gold_count"] for row in rows),
        "baseline": baseline, "reranker": reranker,
        "delta": {name: reranker[name] - value if value is not None else None for name, value in baseline.items()},
    }


def evaluate(
    records: Sequence[dict[str, Any]], predictions: Sequence[dict[str, Any]], *,
    cutoffs: Sequence[int] = (1, 3, 5), bootstrap_samples: int = 0, seed: int = 42,
    confidence: float = 0.95, allow_training: bool = False,
) -> dict[str, Any]:
    if not cutoffs or any(type(k) is not int or k < 1 for k in cutoffs):
        raise ValueError("cutoffs must contain positive integers")
    cutoffs = tuple(sorted(set(cutoffs)))
    if type(bootstrap_samples) is not int or bootstrap_samples < 0:
        raise ValueError("bootstrap_samples must be a nonnegative integer")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0,1)")
    indexed = validate_predictions(records, predictions)
    training_ids = [record["query_id"] for record in records
                    if record.get("split") == "train" or record.get("gold_injected")
                    or record.get("training_candidates")]
    if training_ids and not allow_training:
        raise ValueError("Training candidates or split=train data cannot be used for benchmark evaluation; "
                         "pass --allow-training for an explicitly labeled diagnostic report. "
                         f"Affected queries: {training_ids[:10]}")
    rows, skipped = [], []
    for record in records:
        query_id = record["query_id"]
        gold_list = record.get("positive_page_ids")
        if not isinstance(gold_list, list) or any(not isinstance(page, str) or not page for page in gold_list):
            raise ValueError(f"{query_id}: positive_page_ids must explicitly list ALL gold page IDs")
        if len(set(gold_list)) != len(gold_list):
            raise ValueError(f"{query_id}: duplicate positive_page_ids")
        gold = set(gold_list)
        # Candidate flags are auxiliary; they cannot replace the complete gold list.
        for page in record["candidates"]:
            if "relevant" in page and (type(page["relevant"]) is not bool or page["relevant"] != (page["page_id"] in gold)):
                raise ValueError(f"{query_id}: candidate relevance flag disagrees with positive_page_ids")
        if not gold:
            skipped.append(query_id)
            continue
        baseline = candidate_page_ids(record)
        subset = record.get("subset", "unspecified")
        if not isinstance(subset, str):
            raise ValueError(f"{query_id}: subset must be a string")
        rows.append({
            "query_id": query_id, "subset": subset, "gold_count": len(gold),
            "candidate_count": len(baseline), "gold_in_candidates": len(gold.intersection(baseline)),
            "fallback": indexed[query_id].get("fallback", False),
            "baseline": ranking_metrics(baseline, gold, cutoffs),
            "reranker": ranking_metrics(indexed[query_id]["ranked_page_ids"], gold, cutoffs),
        })
    retrieval_scopes = {}
    for record in records:
        scope = record.get("retrieval_scope", "unspecified")
        if scope not in {"document", "global", "unspecified"}:
            raise ValueError(f"{record['query_id']}: unknown retrieval_scope {scope!r}")
        retrieval_scopes[scope] = retrieval_scopes.get(scope, 0) + 1
    fallback_count = sum(pred["fallback"] for pred in predictions)
    report = {
        "schema_version": 1,
        "evaluation_scope": "training_diagnostic" if training_ids else "retrieval_candidates",
        "training_candidate_queries": training_ids,
        "protocol": {
            "relevance": "binary", "gold_denominator": "all positive_page_ids, including unretrieved pages",
            "no_gold": "skipped from ranking metrics; predictions still required",
            "baseline_order": "input candidates array order", "cutoffs": list(cutoffs),
            "mrr": "first relevant page in the entire candidate ranking",
            "ndcg": "ideal DCG uses all gold pages up to the cutoff",
            "retrieval_scope_queries": retrieval_scopes,
            "fallback_rate_denominator": "all input queries, including queries without gold",
        },
        "input_queries": len(records), "skipped_no_gold": len(skipped), "skipped_query_ids": skipped,
        "fallback_queries": fallback_count,
        "fallback_rate": fallback_count / len(records) if records else None,
        "candidate_recall_ceiling": {
            "macro": sum(row["gold_in_candidates"] / row["gold_count"] for row in rows) / len(rows) if rows else None,
            "micro": sum(row["gold_in_candidates"] for row in rows) / sum(row["gold_count"] for row in rows) if rows else None,
            "definition": "Maximum recall attainable by any permutation of these candidates; all gold pages remain in denominator",
        },
        **_comparison(rows, cutoffs),
        "by_subset": {subset: _comparison([row for row in rows if row["subset"] == subset], cutoffs)
                      for subset in sorted({row["subset"] for row in rows})},
        "per_query": rows,
    }
    if bootstrap_samples:
        report["paired_bootstrap"] = {
            "samples": bootstrap_samples, "seed": seed, "confidence": confidence,
            "unit": "query", "quantity": "reranker_minus_baseline", "method": "percentile",
            "delta_ci": paired_bootstrap(rows, cutoffs, samples=bootstrap_samples, seed=seed, confidence=confidence),
        }
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Original candidate JSONL with complete gold IDs")
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cutoffs", nargs="+", type=int, default=[1, 3, 5])
    parser.add_argument("--bootstrap-samples", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--allow-training", action="store_true", help="Permit train/injected-gold data ONLY as a training diagnostic")
    args = parser.parse_args(argv)
    if args.output.resolve() in {args.input.resolve(), args.predictions.resolve()}:
        parser.error("--output must differ from the input and predictions files")
    report = evaluate(read_jsonl(args.input), read_jsonl(args.predictions), cutoffs=args.cutoffs,
                      bootstrap_samples=args.bootstrap_samples, seed=args.seed, confidence=args.confidence,
                      allow_training=args.allow_training)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"evaluated_queries": report["evaluated_queries"], "baseline": report["baseline"],
                      "reranker": report["reranker"], "delta": report["delta"]}, indent=2))


if __name__ == "__main__":
    main()
