"""Check that student GRPO preparation cannot repair retrieval or reuse SFT data."""
import importlib.util
from pathlib import Path

import pytest

from docreranker.io import page_key

spec = importlib.util.spec_from_file_location("prepare_grpo_data", Path(__file__).resolve().parents[1] / "scripts/prepare_grpo_data.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def query(identifier, document, text=None, split="train"):
    return {"query_id": identifier, "query": text or identifier, "split": split,
            "doc_names": [document], "subset": "ArxivQA", "positive_page_ids": [page_key("ArxivQA", document, 2)]}


def test_select_excludes_normalized_documents_questions_and_test():
    sft = [{"query_id": "sft", "query": "Known question", "page_ids": [page_key("ArxivQA", "Used.pdf", 1)]}]
    candidates = [query("a", " USED "), query("b", "new", " known   QUESTION "),
                  query("c", "test.pdf"), query("d", "fresh"), query("e", "new", "test question")]
    evaluation = [query("test", "Test", "test question", split="evaluation")]
    assert module.select_queries(candidates, sft, evaluation, 1, 42) == [candidates[3]]
    with pytest.raises(ValueError, match="only 1"):
        module.select_queries(candidates, sft, evaluation, 2, 42)


def candidate_row():
    row = query("q", "fresh")
    row.update(retrieval_scope="document", training_candidates=False, gold_injected=False,
               candidates=[{"page_id": page_key("ArxivQA", "fresh", i), "image": f"/page{i}.png",
                            "relevant": False, "score": 5-i} for i in range(1, 6)])
    return row


def test_conversion_uses_gold_ids_and_preserves_order():
    source = candidate_row()
    rows, skipped = module.convert_candidates([source])
    assert rows[0]["positive_ids"] == [2]  # Ignores the deliberately wrong 'relevant' fields.
    assert rows[0]["page_ids"] == [page["page_id"] for page in source["candidates"]]
    assert skipped == []
    assert "messages" not in rows[0]


@pytest.mark.parametrize("field,value", [("gold_injected", True), ("training_candidates", True),
                                         ("split", "evaluation"), ("retrieval_scope", "global")])
def test_conversion_rejects_invalid_protocol(field, value):
    row = candidate_row()
    row[field] = value
    with pytest.raises(ValueError, match="original"):
        module.convert_candidates([row])


def test_missing_gold_is_skipped_without_insertion():
    missing = candidate_row()
    missing["query_id"] = "missed"
    missing["positive_page_ids"] = [page_key("ArxivQA", "fresh", 6)]
    rows, skipped = module.convert_candidates([missing, candidate_row()])
    assert len(rows) == 1
    assert skipped == [{"query_id": "missed", "reason": "no positive-negative ranking signal"}]
