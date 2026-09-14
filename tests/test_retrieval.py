import numpy as np
import pytest
import json
from types import SimpleNamespace

from PIL import Image

from docreranker.io import read_jsonl
from docreranker.retrieval import ColQwenEncoder, build_index, maxsim, retrieve, select_candidates, validate_index_model


def test_late_interaction_matches_hand_calculation():
    query = np.array([[1, 0], [0, 1]])
    doc = np.array([[0.2, 0.3], [0.8, 0.1]])
    assert maxsim(query, doc) == pytest.approx(1.1)


def test_eval_misses_remain_misses_but_training_can_mine_hard_negatives():
    query = {"query_id": "q", "positive_page_ids": ["p3"]}
    pages = [{"page_id": f"p{i}", "image": f"{i}.png"} for i in range(4)]
    eval_row = select_candidates(query, pages, [4, 3, 2, 1], 2)
    assert [c["page_id"] for c in eval_row["candidates"]] == ["p0", "p1"]
    assert eval_row["positive_page_ids"] == ["p3"]
    train_row = select_candidates(query, pages, [4, 3, 2, 1], 2, training=True)
    assert [c["page_id"] for c in train_row["candidates"]] == ["p0", "p3"]
    assert train_row["gold_injected"]
    with pytest.raises(ValueError, match="evaluation"):
        select_candidates({**query, "split": "evaluation"}, pages, [4, 3, 2, 1], 2, training=True)


def test_retrieval_rejects_nan_and_duplicate_pages():
    with pytest.raises(ValueError, match="nonfinite"):
        select_candidates({"positive_page_ids": []}, [{"page_id": "a"}], [float("nan")], 1)
    with pytest.raises(ValueError, match="duplicate"):
        select_candidates({"positive_page_ids": []}, [{"page_id": "a"}] * 2, [1, 2], 1)


def _image_page(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (28, 28), "white").save(path)
    return {"page_id": "p", "image": str(path), "subset": "s", "doc_name": "document"}


class FakeEncoder:
    calls = []

    def __init__(self, model_name, device, max_pixels, revision=None):
        self.calls.append({"model": model_name, "revision": revision})

    def images(self, paths):
        return [np.array([[1.0, 0.0]], dtype=np.float16) for _ in paths]

    def query(self, text):
        return np.array([[1.0, 0.0]], dtype=np.float16)


def test_local_model_change_invalidates_cached_embeddings_and_search(tmp_path, monkeypatch):
    monkeypatch.setattr("docreranker.retrieval.ColQwenEncoder", FakeEncoder)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text('{"test":true}')
    weights = model / "model.safetensors"
    weights.write_bytes(b"initial test weights")
    page = _image_page(tmp_path)
    index = tmp_path / "index"
    first = build_index([page], index, str(model), "cpu", 784, 1)
    assert first["new_embeddings"] == 1
    second = build_index([page], index, str(model), "cpu", 784, 1)
    assert second["new_embeddings"] == 0
    original_config = json.loads((index / "config.json").read_text())
    weights.write_bytes(b"changed test weights at the same model path")
    query = {"query_id": "q", "query": "q", "positive_page_ids": ["p"],
             "subset": "s", "doc_names": ["document"]}
    with pytest.raises(ValueError, match="model or processor changed"):
        retrieve([query], index, tmp_path / "predictions.jsonl", "cpu", 1, "document", False)
    third = build_index([page], index, str(model), "cpu", 784, 1)
    assert third["new_embeddings"] == 1
    updated_config = json.loads((index / "config.json").read_text())
    assert updated_config["model_identity"] != original_config["model_identity"]
    corrupted_config = {**updated_config, "max_pixels": 1568}
    (index / "config.json").write_text(json.dumps(corrupted_config))
    with pytest.raises(ValueError, match="entries and index configuration disagree"):
        retrieve([query], index, tmp_path / "predictions.jsonl", "cpu", 1, "document", False)
    (model / "config.json").write_text('{"test":"changed processor configuration"}')
    with pytest.raises(ValueError, match="model or processor changed"):
        validate_index_model(updated_config)


def test_hub_index_pins_same_immutable_revision_for_pages_and_queries(tmp_path, monkeypatch):
    hub = pytest.importorskip("huggingface_hub")
    commits = iter(["immutable-commit-A", "immutable-commit-B"])
    monkeypatch.setattr(hub.HfApi, "model_info", lambda *args, **kwargs: SimpleNamespace(sha=next(commits)))
    monkeypatch.setattr("docreranker.retrieval.ColQwenEncoder", FakeEncoder)
    FakeEncoder.calls = []
    page = _image_page(tmp_path)
    index = tmp_path / "index"
    build_index([page], index, "org/remote-model", "cpu", 784, 1)
    query = {"query_id": "q", "query": "q", "positive_page_ids": ["p"],
             "subset": "s", "doc_names": ["document"]}
    retrieve([query], index, tmp_path / "predictions.jsonl", "cpu", 1, "document", False)
    assert [call["revision"] for call in FakeEncoder.calls] == ["immutable-commit-A", "immutable-commit-A"]
    rebuilt = build_index([page], index, "org/remote-model", "cpu", 784, 1)
    assert rebuilt["new_embeddings"] == 1
    assert FakeEncoder.calls[-1]["revision"] == "immutable-commit-B"


def test_legacy_or_inconsistent_model_identity_requires_rebuild():
    with pytest.raises(ValueError, match="no model identity"):
        validate_index_model({"model": "org/model", "max_pixels": 784})
    with pytest.raises(ValueError, match="revision.*inconsistent"):
        validate_index_model({"model": "org/model", "model_revision": "new",
                              "model_identity": {"kind": "hub", "repo_id": "org/model", "revision": "old"}})


def test_negative_maxsim_must_not_be_clamped_by_document_padding():
    query = np.eye(2, dtype=np.float16)
    document = np.array([[-0.5, -0.25], [-0.75, -0.5]], dtype=np.float16)
    # Maxima are -0.5 and -0.25; inserting a padded zero vector would incorrectly return 0.
    assert maxsim(query, document) == -0.75


@pytest.mark.parametrize("padding_side", ["left", "right"])
def test_encoder_unpadding_is_batch_invariant_and_keeps_attended_augmentation(padding_side):
    torch = pytest.importorskip("torch")

    class Batch(dict):
        def to(self, device):
            return self

    class Processor:
        def __call__(self, text, **kwargs):
            width = max(map(len, text))
            ids, masks = [], []
            for tokens in text:
                pad = [0] * (width - len(tokens))
                ids.append(pad + tokens if padding_side == "left" else tokens + pad)
                masks.append([0] * len(pad) + [1] * len(tokens) if padding_side == "left"
                             else [1] * len(tokens) + [0] * len(pad))
            return Batch(input_ids=torch.tensor(ids), attention_mask=torch.tensor(masks))

    class Model:
        device = "cpu"

        def __call__(self, input_ids, attention_mask):
            # ID 0 can be a genuine query augmentation token or batch padding.
            # The attention mask, not the token ID or vector value, distinguishes them.
            vectors = torch.tensor([[1.0, -1.0], [-0.5, -0.25], [-0.75, -0.5]])
            return SimpleNamespace(embeddings=vectors[input_ids])

    encoder = ColQwenEncoder.__new__(ColQwenEncoder)
    encoder.torch, encoder.processor, encoder.model = torch, Processor(), Model()
    together = encoder._encode(text=[[1], [1, 2], [1, 0]])
    alone = encoder._encode(text=[[1]])[0]
    np.testing.assert_array_equal(together[0], alone)
    assert [len(x) for x in together] == [1, 2, 2]
    np.testing.assert_array_equal(together[2][-1], [1, -1])
    assert maxsim(np.eye(2), together[0]) == maxsim(np.eye(2), alone) == -0.75


def test_training_preserves_all_gold_before_hard_negatives():
    row = {"query_id": "q", "positive_page_ids": ["gold-low", "gold-high"], "split": "train"}
    pages = [{"page_id": pid, "image": pid + ".png"}
             for pid in ["negative", "gold-high", "second-negative", "gold-low"]]
    result = select_candidates(row, pages, [10.0, 8.0, 7.0, -2.0], 3, training=True)
    assert [(c["page_id"], c["relevant"]) for c in result["candidates"]] == [
        ("negative", False), ("gold-high", True), ("gold-low", True)]
    assert result["positive_page_ids"] == row["positive_page_ids"]
    with pytest.raises(ValueError, match="missing gold"):
        select_candidates(row, pages[:-1], [10.0, 8.0, 7.0], 3, training=True)


def test_document_scope_uses_all_pages_in_document_and_global_training_excludes_heldout(tmp_path, monkeypatch):
    monkeypatch.setattr("docreranker.retrieval.ColQwenEncoder", FakeEncoder)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text('{}')
    pages = []
    for pid, subset, doc in [("a-negative", "s", "train-doc"), ("z-gold", "s", "train-doc"),
                             ("heldout", "s", "test-doc"), ("other-subset", "other", "train-doc")]:
        path = tmp_path / (pid + ".png")
        Image.new("RGB", (28, 28), "white").save(path)
        pages.append({"page_id": pid, "image": str(path), "subset": subset, "doc_name": doc})
    index = tmp_path / "index"
    build_index(pages, index, str(model), "cpu", 784, 2)
    q = {"query_id": "q", "query": "q", "positive_page_ids": ["z-gold"],
         "subset": "s", "doc_names": ["train-doc"]}
    output = tmp_path / "eval.jsonl"
    retrieve([{**q, "split": "evaluation"}], index, output, "cpu", 1, "document", False)
    result = read_jsonl(output)[0]
    assert result["retrieval_pool_size"] == 2  # Full document, not the known-positive page.
    assert result["candidates"][0]["page_id"] == "a-negative"
    assert not result["gold_injected"]
    assert result["positive_page_ids"] == ["z-gold"]
    retrieve([{**q, "split": "train"}], index, output, "cpu", 2, "global", True)
    result = read_jsonl(output)[0]
    assert result["retrieval_pool_size"] == 2
    assert {c["page_id"] for c in result["candidates"]} == {"a-negative", "z-gold"}


def test_unknown_programmatic_scope_cannot_silently_become_global(tmp_path):
    with pytest.raises(ValueError, match="scope"):
        retrieve([], tmp_path / "missing", tmp_path / "out", "cpu", 5, "typo", False)
