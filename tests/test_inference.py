"""Contract tests exercise malformed model outputs and multi-image batch plumbing."""
import contextlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from docreranker.inference import (
    QwenReranker, build_messages, input_fingerprint, parse_ranking,
    prediction_from_response, read_jsonl, write_jsonl_atomic,
)
from docreranker.prompts import SYSTEM_PROMPT, ranking_prompt
from docreranker.training.data import multimodal_messages
from docreranker.training.rewards import parse_completion


def record(query_id="q1", page_ids=("a", "b")):
    return {"query_id": query_id, "query": "Which page contains revenue?",
            "candidates": [{"page_id": page_id, "image": f"/tmp/{page_id}.png", "score": 99 - i,
                            "relevant": page_id == "b"} for i, page_id in enumerate(page_ids)],
            "positive_page_ids": ["b"], "subset": "test"}


@pytest.mark.parametrize("response", [
    "<think>Page 2 gives revenue.</think><answer>[2,1]</answer>",
    " \n<think>Visible evidence.</think> <answer> [ 2, 1 ] </answer>\n",
])
def test_valid_permutation(response):
    assert parse_ranking(response, 2) == [2, 1]


@pytest.mark.parametrize("response", [
    "[2,1]", "<answer>[2,2]</answer>", "<answer>[1]</answer>",
    "<answer>[1,2,3]</answer>", "<answer>[0,1]</answer>",
    "<answer>[2,3]</answer>", "<answer>[true,2]</answer>",
    "<answer>[1.0,2]</answer>", '<answer>["1",2]</answer>',
    "<answer>{}</answer>", "<answer>[1,2,]</answer>",
    "<answer>[1,2]</answer><answer>[2,1]</answer>",
    "<answer>[1,2]</answer><answer>", "<answer>[1,2]</answer></answer>",
    "<answer>1 > 2</answer>", "<answer>null</answer>",
])
def test_invalid_output_is_not_silently_repaired(response):
    response = "<think>Page evidence.</think>" + response
    with pytest.raises(ValueError):
        parse_ranking(response, 2)
    prediction = prediction_from_response(record(), response)
    assert prediction["fallback"] is True
    assert prediction["ranked_page_ids"] == ["a", "b"]
    assert prediction["raw_response"] == response
    assert prediction["fallback_reason"]


def test_prediction_uses_indices_and_preserves_baseline():
    prediction = prediction_from_response(record(), "<think>Page 2 has revenue.</think><answer>[2,1]</answer>", elapsed=2.5)
    assert prediction["ranked_page_ids"] == ["b", "a"]
    assert prediction["baseline_page_ids"] == ["a", "b"]
    assert prediction["fallback"] is False
    assert prediction["elapsed"] == 2.5


def test_gold_and_retrieval_scores_never_enter_prompt():
    messages = build_messages(record())
    text = json.dumps(messages)
    assert "positive_page_ids" not in text and "relevant" not in text and '"score"' not in text
    assert "99" not in text
    images = [item for item in messages[1]["content"] if item["type"] == "image"]
    assert [item["image"] for item in images] == ["/tmp/a.png", "/tmp/b.png"]
    assert "Image 1:" in text and "Image 2:" in text


def test_empty_candidates_do_not_load_model(monkeypatch):
    reranker = QwenReranker()
    monkeypatch.setattr(reranker, "_load", lambda: pytest.fail("empty query loaded a model"))
    prediction = reranker.rerank(record(page_ids=()))
    assert prediction["ranked_page_ids"] == []
    assert not prediction["fallback"]
    assert prediction["generation_skipped"] == "empty_candidates"


def test_duplicate_candidate_ids_and_query_ids_fail():
    with pytest.raises(ValueError, match="duplicate candidate"):
        prediction_from_response(record(page_ids=("a", "a")), "<answer>[1,2]</answer>")
    with pytest.raises(ValueError, match="duplicate query_id"):
        QwenReranker().rerank_batch([record(), record()])


@pytest.mark.parametrize("second_response,second_fallback", [
    ("<think>Page 1 has the evidence.</think><answer>[1]</answer>", False),
    ("malformed or truncated generation", True),
])
def test_multimodal_batch_preserves_image_order_and_strips_entire_padded_prompt(tmp_path, second_response, second_fallback):
    records = [record("q1", ("red", "blue")), record("q2", ("green",)), record("q3", ())]
    for item in records:
        for page in item["candidates"]:
            path = tmp_path / (page["page_id"] + ".png")
            Image.new("RGB", (28, 28), page["page_id"]).save(path)
            page["image"] = str(path)
    observed = {}

    class Batch(dict):
        def to(self, device):
            observed["device"] = device
            return self

    class Processor:
        tokenizer = SimpleNamespace(pad_token_id=0)

        def apply_chat_template(self, message, **kwargs):
            return json.dumps(message)

        def __call__(self, *, text, images, padding, return_tensors):
            observed["texts"] = text
            observed["pixels"] = [image.getpixel((0, 0)) for image in images]
            assert padding and return_tensors == "pt"
            return Batch(input_ids=np.array([[1, 2, 3], [0, 4, 5]]))

        def batch_decode(self, ids, **kwargs):
            assert ids.tolist() == [[9, 8], [7, 6]]
            return ["<think>Page 2 is relevant.</think><answer>[2,1]</answer>", second_response]

    class Model:
        device = "cpu"

        def generate(self, **kwargs):
            assert kwargs["do_sample"] is False
            return np.array([[1, 2, 3, 9, 8], [0, 4, 5, 7, 6]])

    reranker = QwenReranker()
    reranker._processor = Processor()
    reranker._model = Model()
    reranker._torch = SimpleNamespace(inference_mode=contextlib.nullcontext)
    results = reranker.rerank_batch(records)
    assert observed["pixels"] == [(255, 0, 0), (0, 0, 255), (0, 128, 0)]
    assert len(observed["texts"]) == 2
    assert observed["device"] == "cpu"
    assert [item["query_id"] for item in results] == ["q1", "q2", "q3"]
    assert results[0]["ranked_page_ids"] == ["blue", "red"]
    assert results[1]["ranked_page_ids"] == ["green"]
    assert results[1]["fallback"] is second_fallback
    assert results[0]["batch_size"] == 2
    assert results[0]["elapsed"] >= 0
    expected_config = {
        "processor": "Qwen/Qwen2.5-VL-7B-Instruct", "dtype": "auto",
        "min_pixels": 256 * 28 * 28, "max_pixels": 1024 * 28 * 28,
        "max_new_tokens": 768, "do_sample": False, "attn_implementation": "sdpa",
    }
    # Preserve metadata for successful, malformed-output fallback and empty queries.
    assert all(result["inference_config"] == expected_config for result in results)


def test_fingerprint_tracks_query_and_order_but_not_labels():
    original = record()
    edited = json.loads(json.dumps(original))
    edited["positive_page_ids"] = []
    edited["candidates"][0]["relevant"] = True
    assert input_fingerprint(original) == input_fingerprint(edited)
    edited["query"] = "A different question"
    assert input_fingerprint(original) != input_fingerprint(edited)
    edited["query"] = original["query"]
    edited["candidates"].reverse()
    assert input_fingerprint(original) != input_fingerprint(edited)


def test_inference_prompt_exactly_matches_training_text_and_image_slots():
    item = record()
    expected = multimodal_messages([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": ranking_prompt(item["query"], 2)},
    ], 2)
    actual = build_messages(item)
    image_paths = []
    for message in actual:
        for part in message["content"]:
            if part["type"] == "image":
                image_paths.append(part.pop("image"))
    assert actual == expected
    assert image_paths == [candidate["image"] for candidate in item["candidates"]]


@pytest.mark.parametrize("response", [
    "<think>Page 2 contains revenue.</think><answer>[2,1]</answer>",
    "\n<think>Page 1 is relevant.</think>\n<answer>[1, 2]</answer>\n",
    "<answer>[2,1]</answer>",
    "<think>Evidence</think><answer> [2,1] </answer>",
    "<think> </think><answer>[2,1]</answer>",
    "prefix<think>Evidence</think><answer>[2,1]</answer>",
    "<think>Evidence</think><answer>[2,1]</answer>trailing text",
    "<think><answer>[1,2]</answer></think><answer>[2,1]</answer>",
    "<think>Evidence</think><answer>[true,2]</answer>",
    "<think>Evidence</think><answer>[1,1]</answer>",
])
def test_inference_and_training_reward_accept_identical_completions(response):
    trained_order = parse_completion(response, 2)
    if trained_order is None:
        with pytest.raises(ValueError):
            parse_ranking(response, 2)
        assert prediction_from_response(record(), response)["fallback"]
    else:
        assert parse_ranking(response, 2) == trained_order
        assert not prediction_from_response(record(), response)["fallback"]


def test_jsonl_rejects_duplicates(tmp_path):
    path = tmp_path / "input.jsonl"
    path.write_text(json.dumps(record()) + "\n" + json.dumps(record()) + "\n")
    with pytest.raises(ValueError, match="duplicate query_id"):
        read_jsonl(path)


def test_atomic_write_does_not_replace_existing_file_on_generator_failure(tmp_path):
    path = tmp_path / "predictions.jsonl"
    path.write_text("previous results\n")

    def fail_after_one():
        yield {"query_id": "q1"}
        raise RuntimeError("model failed")

    with pytest.raises(RuntimeError, match="model failed"):
        write_jsonl_atomic(path, fail_after_one())
    assert path.read_text() == "previous results\n"
    assert list(tmp_path.iterdir()) == [path]


def test_demo_replays_verified_predictions_and_live_mode_loads_only_on_click(tmp_path, monkeypatch):
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setattr(sys, "argv", ["demo.py"])
    monkeypatch.setattr(QwenReranker, "_load", lambda _: pytest.fail("UI loaded model before button click"))
    item = record()
    for page in item["candidates"]:
        path = tmp_path / (page["page_id"] + ".png")
        Image.new("RGB", (28, 28), "white").save(path)
        page["image"] = str(path)
    candidates = tmp_path / "candidates.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    write_jsonl_atomic(candidates, [item])
    write_jsonl_atomic(predictions, [prediction_from_response(
        item, "<think>Page 2 contains a revenue table.</think><answer>[2,1]</answer>",
    )])
    demo_path = Path(__file__).resolve().parents[1] / "src/docreranker/demo.py"
    app = testing.AppTest.from_file(str(demo_path), default_timeout=20).run()
    assert not app.exception
    app.text_input[0].set_value(str(candidates))
    app.text_input[1].set_value(str(predictions))
    app.run()
    assert not app.exception
    assert not app.error
    assert any("Page 2 contains a revenue table." in entry.value for entry in app.markdown)
    assert len(app.table) == 1
    assert app.text_area[0].disabled
    app.radio[0].set_value("现场重排序").run()
    assert not app.exception
    assert not app.text_area[0].disabled
    app.text_area[0].set_value("A new question").run()
    assert not app.exception
    assert any("复用所选查询的候选页面" in entry.value for entry in app.caption)
