import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest
from PIL import Image

from docreranker.data import convert, document_split, eval_queries, render_pages, save_image, train_queries
from docreranker.io import page_key, read_jsonl, write_jsonl


def test_train_query_ids_do_not_collide_across_subsets():
    row = {"query_id": 0, "query": "q", "positive_passages": [{"doc_name": "x", "page_id": 1}]}
    a = train_queries([row], "ArxivQA")[0]
    b = train_queries([row], "SlideVQA")[0]
    assert a["query_id"] != b["query_id"]
    assert a["positive_page_ids"] != b["positive_page_ids"]


def test_official_exact_duplicate_rows_deduplicate_but_conflicting_ids_fail():
    row = {"query_id": "train_54030", "query": "q",
           "positive_passages": [{"doc_name": "doc", "page_id": "123"}]}
    assert len(train_queries([row, dict(row)], "Wiki-ss")) == 1
    with pytest.raises(ValueError, match="conflicting duplicate"):
        train_queries([row, {**row, "query": "a different question"}], "Wiki-ss")


def test_eval_zero_based_page_offsets_and_inclusive_range():
    doc = {"doc_name": "x.pdf", "page_indices": [8, 10],
           "questions": [{"Q": "q", "page_id": [0, 2]}]}
    query = eval_queries([doc])[0]
    assert query["positive_page_ids"] == [page_key("evaluation", "x.pdf", 0),
                                          page_key("evaluation", "x.pdf", 2)]
    doc["questions"][0]["page_id"] = [3]
    with pytest.raises(ValueError, match="offset"):
        eval_queries([doc])


def test_document_split_keeps_transitively_shared_documents_together():
    rows = [{"query_id": str(i), "subset": "a", "doc_names": docs}
            for i, docs in enumerate([["a"], ["a", "b"], ["b", "c"], ["d"], ["e"]])]
    train, val = document_split(rows, 0.4, 42)
    def docs(qs):
        return {d for q in qs for d in q["doc_names"]}
    assert docs(train).isdisjoint(docs(val))
    assert all(q["split"] == "train" for q in train)
    assert all(q["split"] == "validation" for q in val)
    assert len(train) + len(val) == len(rows)
    assert document_split(rows, 0.4, 42) == (train, val)


def test_convert_eval_parquet_with_real_images(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    blob = io.BytesIO()
    Image.new("RGB", (30, 40), "white").save(blob, format="PNG")
    write_jsonl(tmp_path / "MMDocIR_annotations.jsonl", [{"doc_name": "a.pdf", "page_indices": [0, 1],
                  "questions": [{"Q": "find second", "page_id": [1]}]}])
    pq.write_table(pa.Table.from_pylist([{"doc_name": "a", "image_binary": blob.getvalue(),
                                         "passage_id": str(index)}
                                        for index in range(2)]), tmp_path / "MMDocIR_pages.parquet")
    report = convert(tmp_path, tmp_path / "out", "evaluation", [], None)
    assert report["pages"] == 2 and report["queries"] == 1
    query = read_jsonl(tmp_path / "out/queries.jsonl")[0]
    pages = read_jsonl(tmp_path / "out/pages.jsonl")
    assert query["positive_page_ids"] == [pages[1]["page_id"]]
    with Image.open(pages[0]["image"]) as image:
        assert image.size == (30, 40)


def test_official_eval_cannot_be_used_for_training_split():
    with pytest.raises(ValueError, match="evaluation"):
        document_split([{"split": "evaluation"}], 0.1, 42)


def test_changed_source_image_cannot_reuse_old_pixels_at_same_page_key(tmp_path):
    blobs = []
    for color, dimensions in [("red", (30, 40)), ("blue", (50, 60))]:
        blob = io.BytesIO()
        Image.new("RGB", dimensions, color).save(blob, format="PNG")
        blobs.append(blob.getvalue())
    old, old_width, old_height = save_image(blobs[0], tmp_path, "same-page-id")
    new, width, height = save_image(blobs[1], tmp_path, "same-page-id")
    assert old != new
    assert (old_width, old_height) == (30, 40)
    assert (width, height) == (50, 60)
    assert save_image(blobs[1], tmp_path, "same-page-id")[0] == new
    with Image.open(new) as image:
        assert image.size == (50, 60)
        assert image.getpixel((0, 0)) == (0, 0, 255)
    with Image.open(old) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)


def test_document_split_handles_long_connected_chains_without_recursion():
    rows = [{"query_id": str(i), "subset": "a", "doc_names": [str(i), str(i - 1)]}
            for i in range(1, 1501)]
    rows.append({"query_id": "separate", "subset": "a", "doc_names": ["independent"]})
    train, val = document_split(rows, 0.1, 42)
    assert sorted([len(train), len(val)]) == [1, 1500]
    assert {doc for q in train for doc in q["doc_names"]}.isdisjoint(
        {doc for q in val for doc in q["doc_names"]})


def image_blob(color="red"):
    output = io.BytesIO()
    Image.new("RGB", (30, 40), color).save(output, format="PNG")
    return output.getvalue()


def test_parallel_render_preserves_identity_order_and_reuses_cache(tmp_path, monkeypatch):
    sources = [({"page_id": str(i)}, image_blob(color))
               for i, color in enumerate(("red", "green", "blue", "white", "black"))]
    expected = list(render_pages(sources, tmp_path, 1))
    mtimes = {row["image"]: Path(row["image"]).stat().st_mtime_ns for row in expected}

    def forbid_reencoding(*args, **kwargs):
        pytest.fail("resume reencoded an already complete content-addressed image")

    monkeypatch.setattr(Image.Image, "save", forbid_reencoding)
    assert list(render_pages(sources, tmp_path, 4)) == expected
    assert {path: Path(path).stat().st_mtime_ns for path in mtimes} == mtimes


def test_parallel_render_bounds_consumption_and_returns_source_order(tmp_path, monkeypatch):
    # Block the first page while another worker completes later pages. The
    # producer must stop at four in-flight items rather than materializing all.
    released, first_started, window_filled = threading.Event(), threading.Event(), threading.Event()
    consumed = []

    def save(blob, output, key):
        if key == "0":
            first_started.set()
            assert released.wait(5)
        return str(output / key), 1, 1

    def sources():
        for i in range(20):
            consumed.append(i)
            if not released.is_set():
                assert len(consumed) <= 4
                if len(consumed) == 4:
                    window_filled.set()
            yield {"page_id": str(i)}, b"ignored"

    monkeypatch.setattr("docreranker.data.save_image", save)
    with ThreadPoolExecutor(max_workers=1) as runner:
        future = runner.submit(lambda: list(render_pages(sources(), tmp_path, 2)))
        assert first_started.wait(5)
        assert window_filled.wait(5)
        assert len(consumed) == 4
        assert not future.done()
        released.set()
        actual = future.result(timeout=5)
    assert [row["page_id"] for row in actual] == [str(i) for i in range(20)]


def test_failed_image_write_keeps_previous_file_and_removes_temporary(tmp_path, monkeypatch):
    previous, _, _ = save_image(image_blob(), tmp_path, "p")
    previous_content = Path(previous).read_bytes()
    blue = image_blob("blue")

    def interrupted_save(self, path, **kwargs):
        Path(path).write_bytes(b"incomplete png")
        raise OSError("simulated disk error")

    monkeypatch.setattr(Image.Image, "save", interrupted_save)
    with pytest.raises(OSError, match="disk error"):
        save_image(blue, tmp_path, "p")
    assert Path(previous).read_bytes() == previous_content
    assert list((tmp_path / "images").iterdir()) == [Path(previous)]


def test_failed_parallel_conversion_does_not_publish_partial_metadata(tmp_path, monkeypatch):
    annotation = {"query_id": "1", "query": "question",
                  "positive_passages": [{"doc_name": "doc", "page_id": 0}]}
    write_jsonl(tmp_path / "annotations_top1_negative/ArxivQA_train.jsonl", [annotation])
    out = tmp_path / "out"
    for name in ("pages", "queries"):
        write_jsonl(out / f"{name}.jsonl", [{"previous": name}])
    (out / "manifest.json").write_text('{"previous":true}\n')
    previous = {path.name: path.read_bytes() for path in out.iterdir()}
    rows = [{"file_name": "doc", "page": 0, "image": image_blob()},
            {"file_name": "doc", "page": 1, "image": b"corrupted image"}]
    monkeypatch.setattr("docreranker.data.parquet_rows", lambda *args, **kwargs: iter(rows))
    with pytest.raises(OSError):
        convert(tmp_path, out, "train", ["ArxivQA"], None, image_workers=2)
    assert {name: (out / name).read_bytes() for name in previous} == previous


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_invalid_image_worker_count_fails_before_consuming_source(tmp_path, workers):
    with pytest.raises(ValueError, match="positive integer"):
        list(render_pages([], tmp_path, workers))
