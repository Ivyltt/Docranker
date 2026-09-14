"""ColQwen2 page indexing, late-interaction retrieval, and hard-negative mining."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image

from .io import fingerprint, read_jsonl, unique_index, write_jsonl

DEFAULT_MODEL = "vidore/colqwen2-v1.0-hf"


class UnsuitableTrainingExample(ValueError):
    """Valid dataset row that cannot fit a positive/negative ranking task of size K."""


def maxsim(query: np.ndarray, document: np.ndarray) -> float:
    """Sum, over unpadded query tokens, of the largest document-token dot product."""
    if query.ndim != 2 or document.ndim != 2 or query.shape[1] != document.shape[1]:
        raise ValueError("embeddings must have shapes (tokens, same_dimension)")
    if not len(query) or not len(document):
        raise ValueError("empty token embeddings")
    # Float32 multiplication avoids half-precision accumulation errors on CPU.
    score = (query.astype(np.float32) @ document.astype(np.float32).T).max(axis=1).sum()
    if not np.isfinite(score):
        raise ValueError("nonfinite retrieval score")
    return float(score)


class ColQwenEncoder:
    def __init__(self, model_name=DEFAULT_MODEL, device="cuda", max_pixels=1024 * 28 * 28,
                 revision: str | None = None):
        import torch
        from transformers import ColQwen2ForRetrieval, ColQwen2Processor
        self.torch = torch
        self.processor = ColQwen2Processor.from_pretrained(model_name, max_pixels=max_pixels, revision=revision)
        dtype = torch.bfloat16 if str(device).startswith("cuda") else torch.float32
        self.model = ColQwen2ForRetrieval.from_pretrained(
            model_name, torch_dtype=dtype, device_map=device, attn_implementation="sdpa", revision=revision).eval()

    def _encode(self, **kwargs) -> list[np.ndarray]:
        inputs = self.processor(**kwargs, return_tensors="pt", padding=True).to(self.model.device)
        with self.torch.inference_mode():
            embeddings = self.model(**inputs).embeddings
        mask = inputs["attention_mask"].bool()
        return [embedding[valid].float().cpu().numpy().astype(np.float16)
                for embedding, valid in zip(embeddings, mask)]

    def images(self, paths: list[str]) -> list[np.ndarray]:
        images = []
        try:
            for path in paths:
                with Image.open(path) as image:
                    images.append(image.convert("RGB"))
            return self._encode(images=images)
        finally:
            for image in images:
                image.close()

    def query(self, text: str) -> np.ndarray:
        return self._encode(text=[text])[0]


def page_signature(page: dict) -> dict:
    image = Path(page["image"]).resolve()
    stat = image.stat()
    return {"page_id": page["page_id"], "image": str(image),
            "image_bytes": stat.st_size, "image_mtime_ns": stat.st_mtime_ns}


def local_model_identity(model_name: str) -> dict:
    """Fast identity for local weights, processor, and tokenizer files.

    File metadata deliberately avoids rehashing multi-GB weights on every search.
    Changing a tracked file's size, timestamp, target, or name invalidates the index.
    """
    directory = Path(model_name).resolve()
    if not directory.is_dir():
        raise ValueError(f"local model directory is missing: {directory}")
    suffixes = {".safetensors", ".bin", ".json", ".txt", ".model", ".tiktoken", ".jinja"}
    files = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if any(part.startswith(".") for part in relative.parts) or path.suffix not in suffixes:
            continue
        if not path.is_file():
            continue
        stat = path.stat()
        files.append({"name": str(relative), "resolved": str(path.resolve()),
                      "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    if not files:
        raise ValueError(f"local model has no recognized weights/processor files: {directory}")
    return {"kind": "local", "directory": str(directory), "files": files, "fingerprint": fingerprint(files)}


def resolve_model_identity(model_name: str, revision: str | None = None) -> tuple[str, dict]:
    if Path(model_name).is_dir():
        resolved = str(Path(model_name).resolve())
        return resolved, local_model_identity(resolved)
    if Path(model_name).is_absolute() or model_name.startswith(("./", "../")):
        raise ValueError(f"local model directory is missing: {model_name}")
    from huggingface_hub import HfApi
    commit = HfApi().model_info(model_name, revision=revision or "main").sha
    if not isinstance(commit, str) or not commit:
        raise ValueError(f"could not resolve an immutable Hub revision for {model_name}")
    return model_name, {"kind": "hub", "repo_id": model_name, "revision": commit}


def validate_index_model(config: dict) -> None:
    identity = config.get("model_identity")
    if not isinstance(identity, dict) or identity.get("kind") not in {"local", "hub"}:
        raise ValueError("index has no model identity; rebuild index before retrieval")
    if identity["kind"] == "local":
        if local_model_identity(config["model"]) != identity:
            raise ValueError("local model or processor changed since indexing; rebuild index")
    elif (identity.get("repo_id") != config.get("model") or not identity.get("revision")
          or config.get("model_revision") != identity["revision"]):
        raise ValueError("index Hub model revision is missing or inconsistent; rebuild index")


def build_index(pages: list[dict], output: Path, model_name: str, device: str,
                max_pixels: int, batch_size: int, revision: str | None = None) -> dict:
    unique_index(pages, "page_id")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    output.mkdir(parents=True, exist_ok=True)
    model_name, identity = resolve_model_identity(model_name, revision)
    model_revision = identity.get("revision")
    config = {"model": model_name, "model_identity": identity, "model_revision": model_revision,
              "max_pixels": max_pixels, "implementation": "transformers-4.57.3"}
    entries = []
    for page in pages:
        source = page_signature(page)
        key = fingerprint({"source": source, "config": config})
        entries.append({**page, "image": source["image"], "source": source,
                        "embedding": str((output / f"{key}.npy").resolve())})
    pending = [page for page in entries if not Path(page["embedding"]).exists()]
    if pending:
        encoder = ColQwenEncoder(model_name, device, max_pixels, revision=model_revision)
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset:offset + batch_size]
            for page, embedding in zip(batch, encoder.images([p["image"] for p in batch])):
                path = Path(page["embedding"])
                temporary = path.with_suffix(".tmp")
                with temporary.open("wb") as handle:
                    np.save(handle, embedding, allow_pickle=False)
                os.replace(temporary, path)
            print(f"indexed {min(offset + batch_size, len(pending))}/{len(pending)} new pages", flush=True)
    write_jsonl(output / "pages.jsonl", entries)
    (output / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    return {"pages": len(pages), "new_embeddings": len(pending), "index": str(output.resolve())}


def select_candidates(query: dict, pages: list[dict], scores: list[float], k: int,
                      training: bool = False) -> dict:
    if k < 1 or len(pages) != len(scores):
        raise ValueError("k must be positive and each page must have one score")
    unique_index(pages, "page_id")
    if not all(np.isfinite(scores)):
        raise ValueError("nonfinite retrieval scores")
    gold = set(query["positive_page_ids"])
    ordered = sorted(range(len(pages)), key=lambda i: (-scores[i], str(pages[i]["page_id"])))
    selected = ordered[:k]
    injected = False
    if training:
        if query.get("split", "train") != "train":
            raise ValueError("cannot inject labels into validation/evaluation data")
        missing_gold = gold - {page["page_id"] for page in pages}
        if missing_gold:
            raise ValueError(f"training query is missing gold pages from its search scope: {query['query_id']}: {sorted(missing_gold)}")
        positive = [i for i in ordered if pages[i]["page_id"] in gold]
        if not positive:
            raise ValueError(f"training query has no gold page in its search scope: {query['query_id']}")
        if len(positive) >= k:
            raise UnsuitableTrainingExample("increase top-k: need room for all gold pages and a hard negative")
        negative = [i for i in ordered if pages[i]["page_id"] not in gold]
        if not negative:
            raise UnsuitableTrainingExample("training query has no negative pages")
        selected = sorted(positive + negative[:k - len(positive)], key=lambda i: (-scores[i], str(pages[i]["page_id"])))
        injected = set(selected) != set(ordered[:k])
    candidates = [{"page_id": pages[i]["page_id"], "image": pages[i]["image"],
                   "score": float(scores[i]), "relevant": pages[i]["page_id"] in gold}
                  for i in selected]
    return {**query, "candidates": candidates, "training_candidates": training,
            "gold_injected": injected, "retrieval_pool_size": len(pages)}


def retrieve(queries: list[dict], index: Path, output: Path, device: str, top_k: int,
             scope: str, training: bool, encoder=None) -> dict:
    if scope not in {"document", "global"}:
        raise ValueError("scope must be document or global")
    if type(top_k) is not int or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    unique_index(queries, "query_id")
    pages = read_jsonl(index / "pages.jsonl")
    unique_index(pages, "page_id")
    config = json.loads((index / "config.json").read_text())
    validate_index_model(config)
    for page in pages:
        if page_signature(page) != page["source"]:
            raise ValueError(f"image changed since indexing: {page['image']}; rebuild index")
        expected = fingerprint({"source": page["source"], "config": config}) + ".npy"
        if Path(page["embedding"]).name != expected:
            raise ValueError("embedding entries and index configuration disagree; rebuild index")
    if encoder is None:
        encoder = ColQwenEncoder(config["model"], device, config["max_pixels"], revision=config["model_revision"])
    if training:
        if any(q.get("split", "train") != "train" for q in queries):
            raise ValueError("training candidate mining requires only train queries")
        # A shared embedding index may include held-out documents. Do not expose
        # them to the teacher/student as global hard negatives during training.
        train_documents = {(q["subset"], doc) for q in queries for doc in q["doc_names"]}
        pages = [p for p in pages if (p["subset"], p["doc_name"]) in train_documents]
    by_document = {}
    for page in pages:
        by_document.setdefault((page["subset"], page["doc_name"]), []).append(page)
    rows, skipped = [], []
    for query in queries:
        if scope == "document":
            pool = [p for doc in query["doc_names"]
                    for p in by_document.get((query["subset"], doc), [])]
        else:
            pool = pages
        if not pool:
            raise ValueError(f"no indexed pages for {query['query_id']}")
        # Memory-map one page at a time, never load the whole multivector index to GPU/RAM.
        embedding = encoder.query(query["query"])
        scores = [maxsim(embedding, np.load(p["embedding"], mmap_mode="r", allow_pickle=False)) for p in pool]
        try:
            row = select_candidates(query, pool, scores, top_k, training)
        except UnsuitableTrainingExample as exc:
            skipped.append({"query_id": query["query_id"], "reason": str(exc)})
            continue
        row.update({"retrieval_model": config["model"], "retrieval_scope": scope})
        rows.append(row)
    write_jsonl(output, rows)
    write_jsonl(output.with_suffix(".skipped.jsonl"), skipped)
    return {"queries": len(rows), "candidate_pages": sum(len(r["candidates"]) for r in rows),
            "gold_injected_queries": sum(r["gold_injected"] for r in rows), "scope": scope,
            "skipped_training_queries": len(skipped)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index")
    index.add_argument("--pages", type=Path, required=True)
    index.add_argument("--output", type=Path, required=True)
    index.add_argument("--model", default=DEFAULT_MODEL)
    index.add_argument("--revision", default="main", help="Hub branch/tag/commit, pinned to a commit when indexing")
    index.add_argument("--max-pixels", type=int, default=1024 * 28 * 28)
    index.add_argument("--batch-size", type=int, default=2)
    index.add_argument("--device", default="cuda")
    search = sub.add_parser("search")
    search.add_argument("--queries", type=Path, required=True)
    search.add_argument("--index", type=Path, required=True)
    search.add_argument("--output", type=Path, required=True)
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--scope", choices=["document", "global"], default="document")
    search.add_argument("--training", action="store_true", help="include positives for TRAINING ONLY")
    search.add_argument("--device", default="cuda")
    args = parser.parse_args()
    if args.command == "index":
        report = build_index(read_jsonl(args.pages), args.output, args.model, args.device,
                             args.max_pixels, args.batch_size, revision=args.revision)
    else:
        report = retrieve(read_jsonl(args.queries), args.index, args.output, args.device,
                          args.top_k, args.scope, args.training)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
