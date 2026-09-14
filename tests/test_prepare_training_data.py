import importlib.util
import json
from pathlib import Path

import pytest

from docreranker.io import read_jsonl, write_jsonl


spec = importlib.util.spec_from_file_location(
    "prepare_training_data", Path(__file__).resolve().parents[1] / "scripts/prepare_training_data.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def query(identifier, document, text=None, subset="train"):
    return {"query_id": identifier, "doc_names": [document], "query": text or identifier,
            "subset": subset, "positive_page_ids": [document + ":0"]}


def test_formal_split_excludes_normalized_evaluation_overlap_without_resampling():
    train = [query("keep1", "safe1"), query("doc-overlap", " EVAL.PDF "),
             query("question-overlap", "safe2", " Same\n QUESTION  "), query("keep2", "safe3")]
    validation = [query("v", "validation")]
    evaluation = [query("e", "eval", "same question", "evaluation")]
    before = json.dumps([train, validation])
    safe, val, audit = prepare.formal_splits(train, validation, evaluation)
    assert [row["query_id"] for row in safe] == ["keep1", "keep2"]
    assert val == validation
    assert json.dumps([train, validation]) == before
    assert audit["raw_train_queries"] == 4
    assert audit["formal_train_queries"] == 2
    assert audit["excluded_query_ids"] == ["doc-overlap", "question-overlap"]
    assert audit["remaining_evaluation_overlaps"] == 0


def test_negative_document_overlap_also_excludes_entire_query():
    row = query("q", "positive")
    row["doc_names"].append("negative.PDF")
    safe, _, audit = prepare.formal_splits([row], [], [query("e", "negative")])
    assert safe == []
    assert audit["removed_train"][0]["overlapping_documents"] == ["negative"]


def test_formal_split_rejects_cross_subset_normalized_document_leakage():
    with pytest.raises(ValueError, match="normalized document leakage"):
        prepare.formal_splits([query("t", "Common.PDF", subset="A")],
                             [query("v", "common", subset="B")], [query("e", "eval")])


def test_frozen_selection_refuses_change_without_overwriting_either_file(tmp_path):
    train, val = [query("t", "a")], [query("v", "b")]
    prepare.freeze_preselection(tmp_path, train, val)
    original = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    prepare.freeze_preselection(tmp_path, train, val)
    with pytest.raises(ValueError, match="frozen preselection"):
        prepare.freeze_preselection(tmp_path, train, [query("changed", "c")])
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()} == original
    assert read_jsonl(tmp_path / "train_queries.jsonl") == train


def test_summary_counts_only_query_document_pages():
    pages = [{"page_id": doc + ":" + str(index), "doc_name": doc, "subset": "train"}
             for doc in ("a", "b") for index in range(2)]
    result = prepare.summarize([query("t", "a")], pages)
    assert result["pages"] == 2
    assert result["available_index_pages"] == 4
    assert result["eligible_document_ranking_queries"] == 1


def test_reuse_refuses_incomplete_metadata_even_when_images_exist(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "out"
    raw.mkdir()
    out.mkdir()
    (out / "image.png").write_bytes(b"exists")
    manifest = {"split": "train", "source": str(raw.resolve()), "seed": 42, "limit_queries": 1,
                "pages": 2, "queries": 1}
    (out / "manifest.json").write_text(json.dumps(manifest))
    write_jsonl(out / "pages.jsonl", [{"page_id": "a:0", "image": str(out / "image.png")}])
    write_jsonl(out / "queries.jsonl", [query("t", "a")])
    assert prepare.current_conversion(raw, out, "train", 1, 42) is None
