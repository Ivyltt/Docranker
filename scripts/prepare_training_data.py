"""Prepare real MMDocIR train/validation and full evaluation data on a CPU node.

No API secrets are read and no model/API inference is run by this script.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import tempfile
import time

os.environ["HF_HUB_DISABLE_XET"] = "1"

from docreranker.data import SUBSETS, TRAIN_REPO, convert, document_split, download, eval_queries, train_queries
from docreranker.io import read_jsonl, unique_index, write_jsonl
from docreranker.isolation import documents, evaluation_overlap, normalized_question

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize(queries: list[dict], pages: list[dict] | None = None, top_k: int = 5) -> dict:
    result = {
        "queries": len(queries),
        "queries_by_subset": dict(sorted(Counter(q["subset"] for q in queries).items())),
        "gold_count_distribution": dict(sorted(Counter(str(len(q["positive_page_ids"])) for q in queries).items())),
        "documents": len({(q["subset"], doc) for q in queries for doc in q["doc_names"]}),
        "eligible_by_gold_count": sum(0 < len(q["positive_page_ids"]) < top_k for q in queries),
    }
    if pages is not None:
        wanted_documents = {(q["subset"], doc) for q in queries for doc in q["doc_names"]}
        scoped_pages = [page for page in pages if (page["subset"], page["doc_name"]) in wanted_documents]
        by_doc: dict[tuple, set] = {}
        for page in pages:
            by_doc.setdefault((page["subset"], page["doc_name"]), set()).add(page["page_id"])
        eligible = 0
        for query in queries:
            pool = set().union(*(by_doc.get((query["subset"], doc), set()) for doc in query["doc_names"]))
            gold = set(query["positive_page_ids"])
            eligible += bool(0 < len(gold) < top_k and gold.issubset(pool) and pool - gold)
        result.update(pages=len(scoped_pages), available_index_pages=len(pages),
                      pages_by_subset=dict(sorted(Counter(p["subset"] for p in scoped_pages).items())),
                      eligible_document_ranking_queries=eligible,
                      eligibility_definition=f"1..{top_k - 1} gold pages, all available, and at least one negative page")
    return result


def formal_splits(train: list[dict], validation: list[dict], evaluation: list[dict]):
    """Remove held-out evaluation overlaps without reordering or resampling the selection."""
    unique_index(train + validation, "query_id")
    removed_train = evaluation_overlap(train, evaluation)
    removed_validation = evaluation_overlap(validation, evaluation)
    removed_ids = {row["query_id"] for row in removed_train + removed_validation}
    safe_train = [row for row in train if row["query_id"] not in removed_ids]
    safe_validation = [row for row in validation if row["query_id"] not in removed_ids]
    train_documents = set().union(*(documents(row) for row in safe_train))
    validation_documents = set().union(*(documents(row) for row in safe_validation))
    if train_documents & validation_documents:
        raise ValueError("normalized document leakage between train and validation")
    if evaluation_overlap(safe_train + safe_validation, evaluation):
        raise AssertionError("formal split still contains evaluation overlap")
    audit = {
        "protocol": "fixed preselection followed by normalized document/question evaluation exclusion; no resampling",
        "document_normalization": "strip, casefold, remove a final .pdf; compare across dataset subsets",
        "question_normalization": "collapse whitespace and casefold",
        "raw_train_queries": len(train), "raw_validation_queries": len(validation),
        "formal_train_queries": len(safe_train), "formal_validation_queries": len(safe_validation),
        "removed_train": removed_train, "removed_validation": removed_validation,
        "excluded_query_ids": sorted(removed_ids), "remaining_evaluation_overlaps": 0,
        "normalized_train_validation_document_overlap": 0,
        # Identical question templates over different source documents are recorded,
        # rather than silently changing the predetermined document split.
        "normalized_train_validation_question_overlap": len(
            {normalized_question(q["query"]) for q in safe_train}
            & {normalized_question(q["query"]) for q in safe_validation}),
    }
    return safe_train, safe_validation, audit


def freeze_preselection(output: Path, train: list[dict], validation: list[dict]) -> None:
    """Refuse to replace a different raw selection after pilot annotation has begun."""
    selections = (("train_queries.jsonl", train), ("validation_queries.jsonl", validation))
    for name, rows in selections:
        path = output / name
        if path.exists() and read_jsonl(path) != rows:
            raise ValueError(f"frozen preselection differs from the current source/config: {path}")
    for name, rows in selections:
        path = output / name
        if not path.exists():
            write_jsonl(path, rows)


def current_conversion(raw: Path, output: Path, split: str, limit: int | None, seed: int) -> dict | None:
    required = [output / name for name in ("manifest.json", "pages.jsonl", "queries.jsonl")]
    if not all(path.exists() for path in required):
        return None
    manifest = json.loads(required[0].read_text())
    source = raw / "download_manifest.json"
    source_download = json.loads(source.read_text()) if source.exists() else None
    if (manifest.get("split") == split and manifest.get("limit_queries") == limit
            and manifest.get("seed") == seed and manifest.get("source") == str(raw.resolve())
            and manifest.get("source_download") == source_download):
        rows = read_jsonl(output / "pages.jsonl")
        queries = read_jsonl(output / "queries.jsonl")
        page_index = unique_index(rows, "page_id")
        unique_index(queries, "query_id")
        if (len(rows) == manifest.get("pages") and len(queries) == manifest.get("queries")
                and all(Path(page["image"]).is_file() for page in rows)
                and all(set(query["positive_page_ids"]).issubset(page_index) for query in queries)):
            return manifest
    return None


def choose_preselection(all_queries: list[dict], args) -> tuple[int, list[dict], list[dict]]:
    """One deterministic selection algorithm shared by planning and full preparation."""
    limit = min(args.limit_queries, len(all_queries))
    while True:
        sampled = random.Random(args.seed).sample(all_queries, limit) if limit < len(all_queries) else all_queries
        train, validation = document_split(sampled, args.validation_fraction, args.seed)
        if sum(0 < len(q["positive_page_ids"]) < args.top_k for q in train) >= args.minimum_train_queries:
            return limit, train, validation
        if limit >= len(all_queries):
            raise ValueError("full source data cannot supply the requested eligible training-query count")
        limit = min(limit + 2000, len(all_queries))


def prepare_annotation_preselection(args) -> dict:
    """Only download tiny annotation files; safe alongside the single Parquet download job."""
    from huggingface_hub import HfApi, hf_hub_download
    raw = ROOT / "data/raw/train"
    output = ROOT / "data/preselection"
    manifest = raw / "download_manifest.json"
    first_metadata = raw / ".cache/huggingface/download/annotations_top1_negative/ArxivQA_train.jsonl.metadata"
    if manifest.exists():
        revision = json.loads(manifest.read_text())["revision"]
    elif first_metadata.exists():
        # Same immutable commit already used by the long-running full-data download.
        revision = first_metadata.read_text().splitlines()[0]
    else:
        revision = HfApi().dataset_info(TRAIN_REPO).sha
    all_queries = []
    source_counts = {}
    for subset in SUBSETS:
        filename = f"annotations_top1_negative/{subset}_train.jsonl"
        path = hf_hub_download(repo_id=TRAIN_REPO, repo_type="dataset", filename=filename,
                               revision=revision, local_dir=raw, token=False)
        source_rows = read_jsonl(path)
        converted_queries = train_queries(source_rows, subset)
        source_counts[subset] = {"raw_rows": len(source_rows), "unique_queries": len(converted_queries),
                                 "exact_duplicate_rows_removed": len(source_rows) - len(converted_queries)}
        all_queries.extend(converted_queries)
    unique_index(all_queries, "query_id")
    limit, train, validation = choose_preselection(all_queries, args)
    freeze_preselection(output, train, validation)
    result = {"status": "preselection_ready", "provisional": True, "created_at": utcnow(),
              "source_revision": revision, "seed": args.seed, "selected_query_limit": limit,
              "validation_fraction": args.validation_fraction,
              "full_training_metadata": summarize(all_queries, top_k=args.top_k),
              "source_rows_by_subset": source_counts,
              "train": summarize(train, top_k=args.top_k), "validation": summarize(validation, top_k=args.top_k),
              "train_queries_file": str(output / "train_queries.jsonl"),
              "validation_queries_file": str(output / "validation_queries.jsonl"),
              "limitation": "This raw selection is frozen; formal preparation excludes held-out evaluation overlap without resampling."}
    write_json(output / "manifest.json", result)
    write_json(ROOT / "outputs/training-data-preparation/preselection_ready.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def prepare(args) -> dict:
    report_path = ROOT / "outputs/training-data-preparation/status.json"
    previous = json.loads(report_path.read_text()) if report_path.exists() else {}
    report = {"status": "running", "started_at": utcnow(), "job_id": os.environ.get("SLURM_JOB_ID"),
              "seed": args.seed, "requested_queries": args.limit_queries,
              "minimum_train_queries": args.minimum_train_queries, "top_k": args.top_k,
              "image_workers": args.image_workers, "resumed_from_job": previous.get("job_id")}
    started = time.monotonic()

    def update(phase: str, **values) -> None:
        report.update(phase=phase, updated_at=utcnow(), elapsed_seconds=round(time.monotonic() - started, 2), **values)
        write_json(report_path, report)
        print(json.dumps({"phase": phase, **values}, ensure_ascii=False), flush=True)

    try:
        raw = ROOT / "data/raw/train"
        output = ROOT / "data/train"
        splits = ROOT / "data/splits"
        source_manifest = raw / "download_manifest.json"
        preselection_manifest = ROOT / "data/preselection/manifest.json"
        revision = (json.loads(source_manifest.read_text())["revision"] if source_manifest.exists()
                    else json.loads(preselection_manifest.read_text())["source_revision"]
                    if preselection_manifest.exists() else "main")
        update("downloading_train", existing_download_manifest=source_manifest.exists())
        download_report = download(raw, "train", list(SUBSETS), True, revision)
        eval_raw = ROOT / "data/raw/evaluation"
        eval_output = ROOT / "data/evaluation"
        eval_manifest_path = eval_raw / "download_manifest.json"
        evaluation_download = json.loads(eval_manifest_path.read_text()) if eval_manifest_path.exists() else None
        required_eval = [eval_raw / name for name in ("MMDocIR_annotations.jsonl", "MMDocIR_pages.parquet")]
        if evaluation_download is None or not all(path.exists() for path in required_eval):
            update("downloading_evaluation")
            evaluation_download = download(eval_raw, "evaluation", list(SUBSETS), True,
                                           evaluation_download["revision"] if evaluation_download else "main")
        evaluation_metadata = eval_queries(read_jsonl(eval_raw / "MMDocIR_annotations.jsonl"))
        update("reading_train_metadata", training_download=download_report)
        all_queries = []
        for subset in SUBSETS:
            all_queries.extend(train_queries(read_jsonl(raw / "annotations_top1_negative" / f"{subset}_train.jsonl"), subset))
        unique_index(all_queries, "query_id")
        update("selecting_train_queries", full_training_metadata=summarize(all_queries, top_k=args.top_k))
        limit, original_train, original_validation = choose_preselection(all_queries, args)
        freeze_preselection(ROOT / "data/preselection", original_train, original_validation)
        if preselection_manifest.exists():
            frozen = json.loads(preselection_manifest.read_text())
            if (frozen.get("source_revision") != download_report["revision"]
                    or frozen.get("seed") != args.seed or frozen.get("selected_query_limit") != limit
                    or frozen.get("validation_fraction") != args.validation_fraction):
                raise ValueError("frozen preselection metadata differs from current source/config")
        train, validation, isolation_audit = formal_splits(original_train, original_validation, evaluation_metadata)
        write_json(ROOT / "outputs/training-data-preparation/formal_isolation_audit.json", isolation_audit)
        if len(train) < args.minimum_train_queries:
            raise ValueError("evaluation exclusions leave too few training queries; frozen selection will not be resampled")
        update("converting_train", selected_query_limit=limit, isolation=isolation_audit)
        conversion = current_conversion(raw, output, "train", limit, args.seed)
        if conversion is None:
            conversion = convert(raw, output, "train", list(SUBSETS), limit, args.seed, args.image_workers)
        queries = read_jsonl(output / "queries.jsonl")
        pages = read_jsonl(output / "pages.jsonl")
        converted_train, converted_validation = document_split(queries, args.validation_fraction, args.seed)
        if converted_train != original_train or converted_validation != original_validation:
            raise ValueError("converted query selection differs from the frozen preselection")
        train_stats = summarize(train, pages, args.top_k)
        if train_stats["eligible_document_ranking_queries"] < args.minimum_train_queries:
            raise ValueError("not enough usable training queries after exclusions; frozen selection will not be resampled")
        train_docs = {(q["subset"], d) for q in train for d in q["doc_names"]}
        validation_docs = {(q["subset"], d) for q in validation for d in q["doc_names"]}
        if train_docs & validation_docs:
            raise AssertionError("document leakage between train and validation")
        write_jsonl(splits / "train_queries.jsonl", train)
        write_jsonl(splits / "validation_queries.jsonl", validation)
        # Keep the full conversion for source audit/cache reuse and also expose
        # pools containing only documents used by each formal split.
        write_jsonl(splits / "train_pages.jsonl", (page for page in pages
                                                  if (page["subset"], page["doc_name"]) in train_docs))
        write_jsonl(splits / "validation_pages.jsonl", (page for page in pages
                                                       if (page["subset"], page["doc_name"]) in validation_docs))
        train_ready = {"status": "ready", "ready_at": utcnow(), "source_revision": download_report["revision"],
                       "conversion": conversion, "train": train_stats,
                       "validation": summarize(validation, pages, args.top_k),
                       "shared_documents": 0, "validation_fraction_by_document_group": args.validation_fraction,
                       "isolation": isolation_audit, "frozen_preselection_verified": True,
                       "pages_file": str(output / "pages.jsonl"),
                       "formal_train_pages_file": str(splits / "train_pages.jsonl"),
                       "formal_validation_pages_file": str(splits / "validation_pages.jsonl"),
                       "train_queries_file": str(splits / "train_queries.jsonl"),
                       "validation_queries_file": str(splits / "validation_queries.jsonl")}
        write_json(splits / "manifest.json", train_ready)
        write_json(ROOT / "outputs/training-data-preparation/train_ready.json", train_ready)
        update("train_ready", training=train_ready)

        update("converting_full_evaluation", evaluation_download=evaluation_download)
        eval_conversion = current_conversion(eval_raw, eval_output, "evaluation", None, args.seed)
        if eval_conversion is None:
            eval_conversion = convert(eval_raw, eval_output, "evaluation", list(SUBSETS), None, args.seed,
                                      args.image_workers)
        evaluation_queries = read_jsonl(eval_output / "queries.jsonl")
        eval_pages = read_jsonl(eval_output / "pages.jsonl")
        if evaluation_queries != evaluation_metadata:
            raise ValueError("converted evaluation queries differ from the official annotation metadata")
        if any(query["split"] != "evaluation" for query in evaluation_queries):
            raise AssertionError("evaluation rows have an incorrect split marker")
        report["status"] = "complete"
        update("complete", finished_at=utcnow(), evaluation={"conversion": eval_conversion,
                                                            "summary": summarize(evaluation_queries, eval_pages, args.top_k)})
        return report
    except Exception as exc:
        report["status"] = "failed"
        update("failed", error_type=type(exc).__name__, error=str(exc))
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-queries", type=int, default=10000)
    parser.add_argument("--minimum-train-queries", type=int, default=7200)
    parser.add_argument("--validation-fraction", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--image-workers", type=int, default=4, help="Bounded PNG encoding threads; source row groups remain on the CPU node")
    parser.add_argument("--preselect-only", action="store_true", help="Download only annotation files and expose the fixed train/validation selection")
    args = parser.parse_args()
    if args.limit_queries < 1 or args.minimum_train_queries < 1 or args.top_k < 2 or args.image_workers < 1:
        parser.error("query/worker limits must be positive and top-k must be at least 2")
    if not 0 < args.validation_fraction < 1:
        parser.error("validation fraction must be between 0 and 1")
    if args.preselect_only:
        prepare_annotation_preselection(args)
    else:
        prepare(args)


if __name__ == "__main__":
    main()
