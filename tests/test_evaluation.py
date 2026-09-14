"""Protect denominator correctness, complete coverage, and paired comparisons."""
import copy
import math

import pytest

from docreranker.evaluation import evaluate, ranking_metrics, validate_predictions
from docreranker.inference import prediction_from_response


def query(query_id, candidate_ids, gold, subset="test"):
    return {"query_id": query_id, "query": f"Question {query_id}", "subset": subset,
            "candidates": [{"page_id": page, "image": f"/tmp/{page}.png", "score": 1.0,
                            "relevant": page in gold} for page in candidate_ids], "positive_page_ids": gold}


def prediction(record, ranking=None, **kwargs):
    baseline = [page["page_id"] for page in record["candidates"]]
    return {"query_id": record["query_id"], "ranked_page_ids": baseline if ranking is None else ranking,
            "baseline_page_ids": baseline, "fallback": False, **kwargs}


def test_recall_is_not_hit_rate_when_multiple_gold_pages_exist():
    values = ranking_metrics(["a", "b"], {"a", "c", "d"})
    assert values["recall@1"] == pytest.approx(1 / 3)
    assert values["hit@1"] == 1
    assert values["recall@5"] == pytest.approx(1 / 3)
    assert values["ndcg@3"] == pytest.approx(1 / (1 + 1 / math.log2(3) + 0.5))


def test_macro_and_micro_recall_have_distinct_correct_denominators():
    records = [query("q1", ["b", "a"], ["a", "c", "d"]), query("q2", ["z"], ["z"])]
    report = evaluate(records, [prediction(records[0], ["a", "b"]), prediction(records[1])])
    assert report["baseline"]["macro_recall@1"] == 0.5
    assert report["baseline"]["micro_recall@1"] == 0.25
    assert report["reranker"]["macro_recall@1"] == pytest.approx((1 / 3 + 1) / 2)
    assert report["reranker"]["micro_recall@1"] == 0.5
    assert report["reranker"]["hit@1"] == 1.0
    assert report["reranker"]["mrr"] == 1.0
    assert report["total_gold_pages"] == 4
    assert report["per_query"][0]["gold_in_candidates"] == 1


def test_all_unretrieved_gold_and_empty_candidates_score_zero():
    records = [query("q1", ["irrelevant"], ["missing"]), query("q2", [], ["a", "b"])]
    report = evaluate(records, [prediction(record) for record in records])
    assert report["evaluated_queries"] == 2
    assert report["total_gold_pages"] == 3
    assert all(value == 0 for value in report["baseline"].values())


def test_no_gold_queries_skipped_but_must_have_predictions():
    records = [query("q1", ["a"], ["a"]), query("no-labels", ["x"], [])]
    with pytest.raises(ValueError, match="missing"):
        evaluate(records, [prediction(records[0])])
    report = evaluate(records, [prediction(record) for record in records])
    assert report["input_queries"] == 2
    assert report["evaluated_queries"] == 1
    assert report["skipped_no_gold"] == 1
    assert report["skipped_query_ids"] == ["no-labels"]


def test_all_no_gold_reports_null_not_perfect_or_nan_metrics():
    records = [query("q", [], [])]
    report = evaluate(records, [prediction(records[0])], bootstrap_samples=10)
    assert report["evaluated_queries"] == 0
    assert all(value is None for value in report["baseline"].values())
    assert report["paired_bootstrap"]["delta_ci"] == {}


@pytest.mark.parametrize("kind", ["missing", "extra", "duplicate_input", "duplicate_prediction"])
def test_query_coverage_cannot_silently_improve_scores(kind):
    records = [query("q1", ["a"], ["a"]), query("q2", ["b"], ["b"])]
    predictions = [prediction(record) for record in records]
    if kind == "missing":
        predictions.pop()
    elif kind == "extra":
        predictions.append({"query_id": "unknown", "ranked_page_ids": []})
    elif kind == "duplicate_input":
        records.append(copy.deepcopy(records[0]))
    else:
        predictions.append(copy.deepcopy(predictions[0]))
    with pytest.raises(ValueError):
        evaluate(records, predictions)


@pytest.mark.parametrize("ranking", [["a"], ["a", "a"], ["b", "unknown"], ["a", "b", "extra"]])
def test_predictions_must_preserve_candidate_universe(ranking):
    item = query("q", ["a", "b"], ["a"])
    with pytest.raises(ValueError, match="permutation"):
        evaluate([item], [prediction(item, ranking)])


def test_stale_baseline_or_fingerprint_and_false_fallback_rejected():
    item = query("q", ["a", "b"], ["a"])
    for changes in ({"baseline_page_ids": ["b", "a"]}, {"input_fingerprint": "stale"},
                    {"ranked_page_ids": ["b", "a"], "fallback": True}):
        with pytest.raises(ValueError):
            validate_predictions([item], [{**prediction(item), **changes}])


def test_gold_list_is_explicit_and_consistent():
    item = query("q", ["a"], ["a"])
    item.pop("positive_page_ids")
    with pytest.raises(ValueError, match="ALL gold"):
        evaluate([item], [prediction(item)])
    item["positive_page_ids"] = ["a", "a"]
    with pytest.raises(ValueError, match="duplicate positive"):
        evaluate([item], [prediction(item)])
    item["positive_page_ids"] = []
    with pytest.raises(ValueError, match="relevance flag"):
        evaluate([item], [prediction(item)])


def test_paired_bootstrap_reproducible_and_subsets_are_separate():
    records = [query("q1", ["b", "a"], ["a"], "one"), query("q2", ["z"], ["z"], "two")]
    predictions = [prediction(records[0], ["a", "b"]), prediction(records[1])]
    first = evaluate(records, predictions, bootstrap_samples=200, seed=71)
    second = evaluate(records, predictions, bootstrap_samples=200, seed=71)
    assert first["paired_bootstrap"] == second["paired_bootstrap"]
    assert first["paired_bootstrap"]["delta_ci"]["macro_recall@1"] == {"low": 0.0, "high": 1.0}
    assert first["paired_bootstrap"]["delta_ci"]["macro_recall@3"] == {"low": 0.0, "high": 0.0}
    assert first["by_subset"]["one"]["delta"]["mrr"] == 0.5
    assert first["by_subset"]["two"]["delta"]["mrr"] == 0


def test_fallback_is_included_in_metrics():
    item = query("q", ["b", "a"], ["a"])
    report = evaluate([item], [prediction(item, fallback=True)])
    assert report["fallback_queries"] == 1
    assert report["baseline"] == report["reranker"]
    assert report["reranker"]["mrr"] == 0.5


@pytest.mark.parametrize("flag", ["gold_injected", "training_candidates"])
def test_training_candidates_cannot_masquerade_as_benchmark_results(flag):
    item = query("q", ["b", "a"], ["a"])
    item[flag] = True
    with pytest.raises(ValueError, match="Training candidates"):
        evaluate([item], [prediction(item)])
    report = evaluate([item], [prediction(item)], allow_training=True)
    assert report["evaluation_scope"] == "training_diagnostic"
    assert report["training_candidate_queries"] == ["q"]


def test_train_split_without_injection_is_still_only_a_diagnostic():
    item = query("q", ["b", "a"], ["a"])
    item.update(split="train", training_candidates=False, gold_injected=False)
    with pytest.raises(ValueError, match="split=train"):
        evaluate([item], [prediction(item)])
    assert evaluate([item], [prediction(item)], allow_training=True)["evaluation_scope"] == "training_diagnostic"


def test_missing_fallback_is_not_reported_as_success():
    item = query("q", ["a"], ["a"])
    pred = prediction(item)
    del pred["fallback"]
    with pytest.raises(ValueError, match="fallback must explicitly"):
        evaluate([item], [pred])


def test_saved_ranking_and_fallback_must_agree_with_raw_generation():
    item = query("q", ["a", "b"], ["a"])
    valid = prediction_from_response(item, "<think>Page 2 has evidence.</think><answer>[2,1]</answer>")
    evaluate([item], [valid])
    with pytest.raises(ValueError, match="raw_response"):
        evaluate([item], [{**valid, "raw_response": "<think>Page 1 has evidence.</think><answer>[1,2]</answer>"}])
    with pytest.raises(ValueError, match="raw_response"):
        evaluate([item], [{**valid, "raw_response": "truncated generation"}])


def test_three_ablations_share_labels_and_candidate_recall_ceiling():
    records = [query("multi", ["neg", "a", "b", "x", "y"], ["a", "b", "missing"]),
               query("single", ["neg2", "c"], ["c"])]
    for row in records:
        row.update(split="evaluation", retrieval_scope="document")
    generations = {
        "base": ["bad output", "<answer>[1,2]</answer>"],
        "sft": ["<answer>[2,1,3,4,5]</answer>", "<answer>[2,1]</answer>"],
        "grpo": ["<answer>[2,3,1,4,5]</answer>", "<answer>[2,1]</answer>"],
    }
    reports = {name: evaluate(records, [prediction_from_response(row, "<think>Page evidence.</think>" + text)
                                        for row, text in zip(records, responses)])
               for name, responses in generations.items()}
    assert reports["base"]["fallback_queries"] == 1
    assert reports["base"]["fallback_rate"] == 0.5
    assert reports["base"]["reranker"]["macro_recall@1"] == 0
    assert reports["sft"]["reranker"]["macro_recall@1"] == pytest.approx(2 / 3)
    assert reports["grpo"]["reranker"]["micro_recall@1"] == 0.5
    for report in reports.values():
        assert report["baseline"] == reports["base"]["baseline"]
        assert report["protocol"]["retrieval_scope_queries"] == {"document": 2}
        assert report["candidate_recall_ceiling"]["macro"] == pytest.approx(5 / 6)
        assert report["candidate_recall_ceiling"]["micro"] == 0.75
        assert report["reranker"]["macro_recall@5"] == pytest.approx(5 / 6)
        assert report["reranker"]["micro_recall@5"] == 0.75
        assert [r["gold_count"] for r in report["per_query"]] == [3, 1]
    assert reports["grpo"]["reranker"]["ndcg@3"] > reports["sft"]["reranker"]["ndcg@3"]
