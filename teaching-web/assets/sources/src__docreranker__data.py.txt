"""Download official MMDocIR artifacts and convert their two distinct schemas.

The evaluation page_id is a ZERO-based offset inside an inclusive page_indices
range, as used by the official search.py. It is not the Parquet passage_id.
"""
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
import random
import tempfile
from pathlib import Path
from typing import Iterable

from PIL import Image

from .io import page_key, read_jsonl, unique_index, write_jsonl

SUBSETS = ("ArxivQA", "DUDE", "MP-DocVQA", "SciQAG", "SlideVQA", "TAT-DQA", "Wiki-ss")
TRAIN_REPO = "MMDocIR/MMDocIR_Train_Dataset"
EVAL_REPO = "MMDocIR/MMDocIR_Evaluation_Dataset"


def download(output: Path, split: str, subsets: list[str], execute: bool, revision: str) -> dict:
    from huggingface_hub import HfApi, hf_hub_download

    repo = TRAIN_REPO if split == "train" else EVAL_REPO
    names = ([name for subset in subsets for name in
              (f"annotations_top1_negative/{subset}_train.jsonl", f"parquet/{subset}_filter.parquet")]
             if split == "train" else ["MMDocIR_annotations.jsonl", "MMDocIR_pages.parquet"])
    info = HfApi().dataset_info(repo, revision=revision, files_metadata=True)
    sizes = {file.rfilename: file.size or 0 for file in info.siblings}
    plan = {"repo_id": repo, "revision": info.sha, "files": names,
            "bytes": sum(sizes.get(name, 0) for name in names), "executed": execute}
    if execute:
        output.mkdir(parents=True, exist_ok=True)
        for name in names:
            hf_hub_download(repo_id=repo, repo_type="dataset", filename=name,
                            revision=info.sha, local_dir=output)
        (output / "download_manifest.json").write_text(json.dumps(plan, indent=2) + "\n")
    return plan


def parquet_rows(path: Path, columns: list[str] | None = None):
    import pyarrow.parquet as pq
    # Arrow may still retain a large compressed column chunk from a row group;
    # use a CPU allocation for image columns even with small emitted batches.
    parquet = pq.ParquetFile(path)
    if columns is not None:
        # passage_id is optional for evaluation; required fields are checked by
        # the source adapters when each row is consumed.
        columns = [column for column in columns if column in parquet.schema_arrow.names]
    for batch in parquet.iter_batches(batch_size=16, columns=columns):
        yield from batch.to_pylist()


def save_image(blob, output: Path, key: str) -> tuple[str, int, int]:
    if isinstance(blob, dict):
        if blob.get("bytes") is not None:
            blob = blob["bytes"]
        elif blob.get("path"):
            blob = Path(blob["path"]).read_bytes()
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise ValueError(f"missing image bytes for {key}")
    content_hash = hashlib.sha256(bytes(blob)).hexdigest()
    target = output / "images" / (hashlib.sha256(key.encode()).hexdigest() + "-" + content_hash + ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(bytes(blob))) as image:
        width, height = image.size
        if not target.exists():
            fd, temporary = tempfile.mkstemp(dir=target.parent, suffix=".png")
            os.close(fd)
            try:
                with image.convert("RGB") as converted:
                    converted.save(temporary, format="PNG")
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    return str(target.resolve()), width, height


def render_pages(sources: Iterable[tuple[dict, object]], output: Path, image_workers: int = 1):
    """Encode independent pages in parallel with at most 2 * workers in flight.

    Results retain source order. Failed work can leave reusable image cache files,
    but callers publish their metadata only after this iterator fully succeeds.
    """
    if type(image_workers) is not int or image_workers < 1:
        raise ValueError("image_workers must be a positive integer")

    def render(source):
        page, blob = source
        image, width, height = save_image(blob, output, page["page_id"])
        return {**page, "image": image, "width": width, "height": height}

    if image_workers == 1:
        for source in sources:
            yield render(source)
        return
    pending = deque()
    executor = ThreadPoolExecutor(max_workers=image_workers, thread_name_prefix="docreranker-image")
    try:
        for source in sources:
            pending.append(executor.submit(render, source))
            if len(pending) >= image_workers * 2:
                yield pending.popleft().result()
        while pending:
            yield pending.popleft().result()
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)


def train_queries(rows: list[dict], subset: str) -> list[dict]:
    result = []
    seen_source_rows: dict[str, dict] = {}
    for number, row in enumerate(rows):
        query_id = f"train:{subset}:{row.get('query_id', number)}"
        if query_id in seen_source_rows:
            if row == seen_source_rows[query_id]:
                # The official Wiki-ss release contains exact duplicated source rows.
                continue
            raise ValueError(f"conflicting duplicate query_id: {query_id}")
        seen_source_rows[query_id] = row
        positives = row["positive_passages"]
        negatives = row.get("negative_passages", [])
        docs = sorted({str(p["doc_name"]) for p in positives + negatives})
        result.append({"query_id": query_id,
                       "query": row["query"], "subset": subset, "split": "train",
                       "doc_names": docs,
                       "positive_page_ids": sorted({page_key(subset, p["doc_name"], p["page_id"])
                                                    for p in positives})})
    unique_index(result, "query_id")
    return result


def eval_queries(documents: list[dict]) -> list[dict]:
    result = []
    for document in documents:
        start, end = document["page_indices"]
        for index, question in enumerate(document["questions"]):
            ids = question["page_id"]
            if any(type(p) is not int or p < 0 or p > end - start for p in ids):
                raise ValueError(f"invalid eval page offset in {document['doc_name']}")
            result.append({"query_id": f"eval:{document['doc_name']}:{index}",
                           "query": question["Q"], "subset": "evaluation", "split": "evaluation",
                           "domain": document.get("domain", ""), "doc_names": [document["doc_name"]],
                           "positive_page_ids": sorted({page_key("evaluation", document["doc_name"], p)
                                                        for p in ids})})
    unique_index(result, "query_id")
    return result


def convert(raw: Path, output: Path, split: str, subsets: list[str], limit_queries: int | None,
            seed: int = 42, image_workers: int = 1) -> dict:
    if split not in {"train", "evaluation"}:
        raise ValueError("split must be train or evaluation")
    if type(image_workers) is not int or image_workers < 1:
        raise ValueError("image_workers must be a positive integer")
    pages, queries = [], []
    rng = random.Random(seed)
    if split == "train":
        for subset in subsets:
            queries.extend(train_queries(read_jsonl(raw / "annotations_top1_negative" /
                                                   f"{subset}_train.jsonl"), subset))
        if limit_queries is not None and len(queries) > limit_queries:
            queries = rng.sample(queries, limit_queries)
        # Include all pages of selected documents for real hard-negative retrieval.
        wanted = {(q["subset"], doc) for q in queries for doc in q["doc_names"]}

        def train_sources():
            for subset in subsets:
                for row in parquet_rows(raw / "parquet" / f"{subset}_filter.parquet",
                                        columns=["file_name", "page", "image"]):
                    doc, number = str(row["file_name"]), int(row["page"])
                    if (subset, doc) not in wanted:
                        continue
                    key = page_key(subset, doc, number)
                    yield {"page_id": key, "doc_name": doc, "page_number": number,
                           "subset": subset}, row["image"]

        sources = train_sources()
    else:
        documents = read_jsonl(raw / "MMDocIR_annotations.jsonl")
        queries = eval_queries(documents)
        if limit_queries is not None and len(queries) > limit_queries:
            queries = rng.sample(queries, limit_queries)
        wanted = {doc for q in queries for doc in q["doc_names"]}
        row_mapping = {}
        for document in documents:
            if document["doc_name"] not in wanted:
                continue
            start, end = document["page_indices"]
            for index in range(start, end + 1):
                if index in row_mapping:
                    raise ValueError("overlapping eval page_indices")
                row_mapping[index] = (document["doc_name"], index - start)

        def evaluation_sources():
            for index, row in enumerate(parquet_rows(raw / "MMDocIR_pages.parquet",
                                                     columns=["doc_name", "passage_id", "image_binary"])):
                if index not in row_mapping:
                    continue
                doc, number = row_mapping[index]
                # Official Parquet drops the final '.pdf' present in annotations.
                if str(row["doc_name"]).removesuffix(".pdf") != doc.removesuffix(".pdf"):
                    raise ValueError(f"eval Parquet/annotation mismatch at row {index}")
                if "passage_id" in row and int(row["passage_id"]) != number:
                    raise ValueError(f"eval page numbering mismatch at row {index}")
                key = page_key("evaluation", doc, number)
                yield {"page_id": key, "doc_name": doc, "page_number": number,
                       "subset": "evaluation"}, row["image_binary"]

        sources = evaluation_sources()
    for page in render_pages(sources, output, image_workers):
        pages.append(page)
        if len(pages) % 1000 == 0:
            print(f"converted {len(pages)} {split} pages", flush=True)
    page_index = unique_index(pages, "page_id")
    for query in queries:
        missing = set(query["positive_page_ids"]) - page_index.keys()
        if missing:
            raise ValueError(f"missing gold pages for {query['query_id']}: {sorted(missing)}")
    write_jsonl(output / "pages.jsonl", pages)
    write_jsonl(output / "queries.jsonl", queries)
    manifest = {"split": split, "subsets": subsets, "queries": len(queries), "pages": len(pages),
                "seed": seed, "limit_queries": limit_queries, "source": str(raw.resolve()),
                "image_workers": image_workers,
                "evaluation_page_numbering": "zero-based offset in inclusive page_indices"}
    provenance = raw / "download_manifest.json"
    if provenance.exists():
        manifest["source_download"] = json.loads(provenance.read_text())
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def document_split(queries: list[dict], validation_fraction: float, seed: int) -> tuple[list, list]:
    """Connected document groups prevent leakage for questions spanning several docs."""
    if not 0 < validation_fraction < 1:
        raise ValueError("validation fraction must lie between 0 and 1")
    if any(q.get("split") == "evaluation" for q in queries):
        raise ValueError("official evaluation data cannot enter training split")
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != x:
            next_parent = parent[x]
            parent[x] = root
            x = next_parent
        return root

    doc_lists = []
    for query in queries:
        docs = [(query["subset"], doc) for doc in query["doc_names"]]
        if not docs:
            raise ValueError("query must identify its source document")
        for doc in docs:
            parent[find(doc)] = find(docs[0])
        doc_lists.append(docs)
    groups = {}
    for query, docs in zip(queries, doc_lists):
        groups.setdefault(find(docs[0]), []).append(query)
    keys = sorted(groups)
    if len(keys) < 2:
        raise ValueError("at least two independent document groups required")
    random.Random(seed).shuffle(keys)
    count = max(1, min(len(keys) - 1, round(len(keys) * validation_fraction)))
    val_keys = set(keys[:count])
    train = [{**q, "split": "train"} for key in keys if key not in val_keys for q in groups[key]]
    validation = [{**q, "split": "validation"} for key in keys if key in val_keys for q in groups[key]]
    return train, validation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("download", "convert"):
        child = sub.add_parser(name)
        child.add_argument("--split", choices=["train", "evaluation"], required=True)
        child.add_argument("--subsets", nargs="+", choices=SUBSETS, default=list(SUBSETS))
        child.add_argument("--output", type=Path, required=True)
        if name == "download":
            child.add_argument("--execute", action="store_true", help="download after showing a size-only plan")
            child.add_argument("--revision", default="main")
        else:
            child.add_argument("--raw", type=Path, required=True)
            child.add_argument("--limit-queries", type=int)
            child.add_argument("--seed", type=int, default=42)
            child.add_argument("--image-workers", type=int, default=1, help="Bounded concurrent PNG encoders")
    split = sub.add_parser("split")
    split.add_argument("--input", type=Path, required=True)
    split.add_argument("--output", type=Path, required=True)
    split.add_argument("--validation-fraction", type=float, default=0.1)
    split.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.command == "download":
        report = download(args.output, args.split, args.subsets, args.execute, args.revision)
    elif args.command == "convert":
        if args.limit_queries is not None and args.limit_queries <= 0:
            parser.error("--limit-queries must be positive")
        report = convert(args.raw, args.output, args.split, args.subsets, args.limit_queries, args.seed,
                         args.image_workers)
    else:
        train, val = document_split(read_jsonl(args.input), args.validation_fraction, args.seed)
        write_jsonl(args.output / "train_queries.jsonl", train)
        write_jsonl(args.output / "validation_queries.jsonl", val)
        report = {"train_queries": len(train), "validation_queries": len(val)}
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
