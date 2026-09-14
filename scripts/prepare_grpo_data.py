"""Select unused training documents, then convert real retrieval candidates for GRPO.

Selection uses metadata only. Conversion preserves retrieval order and never
inserts gold pages. Neither command loads a model or calls an API.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random

from docreranker.io import read_jsonl, unique_index, write_jsonl
from docreranker.isolation import documents, evaluation_overlap, normalized_document, normalized_question
from docreranker.training.data import load_rows


def select_queries(queries, sft, evaluation, limit, seed):
    unique_index(queries, "query_id")
    used_ids = {row["query_id"] for row in sft}
    used_questions = {normalized_question(row["query"]) for row in sft}
    used_documents = set()
    for row in sft:
        # Annotation stores global page IDs as [subset, document, page].
        for page_id in row["page_ids"]:
            key = json.loads(page_id)
            if not isinstance(key, list) or len(key) != 3:
                raise ValueError("invalid SFT global page ID")
            used_documents.add(normalized_document(key[1]))
    overlaps = {row["query_id"] for row in evaluation_overlap(queries, evaluation)}
    eligible = []
    for row in queries:
        if row.get("split") != "train":
            raise ValueError("selection requires training queries")
        if (row["query_id"] in used_ids or row["query_id"] in overlaps
                or normalized_question(row["query"]) in used_questions
                or documents(row) & used_documents):
            continue
        eligible.append(row)
    if limit is not None and limit > len(eligible):
        raise ValueError(f"requested {limit} queries, but only {len(eligible)} unused, isolated queries remain; lower --limit or prepare a larger training pool")
    random.Random(seed).shuffle(eligible)
    return eligible if limit is None else eligible[:limit]


def convert_candidates(candidates):
    unique_index(candidates, "query_id")
    rows, skipped = [], []
    for row in candidates:
        if (row.get("split") != "train" or row.get("gold_injected")
                or row.get("training_candidates") or row.get("retrieval_scope") != "document"):
            raise ValueError("GRPO requires original, document-scoped training retrieval without gold insertion")
        pages = row["candidates"]
        if len(pages) != 5 or len({page["page_id"] for page in pages}) != 5:
            raise ValueError("expected five distinct retrieved pages")
        gold = set(row["positive_page_ids"])
        positives = [i for i, page in enumerate(pages, 1) if page["page_id"] in gold]
        # This implementation's result reward requires at least one positive.
        # Report an unusable group rather than repairing retrieval with gold.
        if not positives or len(positives) == len(pages):
            skipped.append({"query_id": row["query_id"], "reason": "no positive-negative ranking signal"})
            continue
        rows.append({"query_id": row["query_id"], "query": row["query"],
                     "split": "train", "subset": row["subset"],
                     "images": [page["image"] for page in pages],
                     "positive_ids": positives, "page_ids": [page["page_id"] for page in pages]})
    if not rows:
        raise ValueError("no usable GRPO groups; retrieve more unused training queries")
    return rows, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select", help="select questions from documents not used by SFT")
    select.add_argument("--queries", type=Path, required=True)
    select.add_argument("--sft", type=Path, required=True)
    select.add_argument("--evaluation-queries", type=Path, required=True)
    select.add_argument("--limit", type=int, help="number of questions to retrieve; default: all eligible")
    select.add_argument("--seed", type=int, default=42)
    build = sub.add_parser("build", help="convert original retrieved pages into the GRPO training schema")
    build.add_argument("--input", type=Path, required=True)
    for child in (select, build):
        child.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        inputs = [args.queries, args.sft, args.evaluation_queries] if args.command == "select" else [args.input]
        if args.output.resolve() in {path.resolve() for path in inputs}:
            raise ValueError("output must not overwrite an input")
        if args.command == "select":
            if args.limit is not None and args.limit < 1:
                raise ValueError("--limit must be positive")
            rows = select_queries(read_jsonl(args.queries), read_jsonl(args.sft),
                                  read_jsonl(args.evaluation_queries), args.limit, args.seed)
            if not rows:
                raise ValueError("no unused isolated training queries remain")
            write_jsonl(args.output, rows)
            print(json.dumps({"selected_queries": len(rows), "output": str(args.output)}))
        else:
            skipped_path = args.output.with_suffix(".skipped.jsonl")
            if skipped_path.resolve() in {path.resolve() for path in inputs}:
                raise ValueError("skipped-group report must not overwrite an input")
            rows, skipped = convert_candidates(read_jsonl(args.input))
            # Validate every image and label before publishing the output.
            temporary = args.output.with_suffix(".validation.jsonl")
            if temporary.resolve() in {path.resolve() for path in inputs}:
                raise ValueError("validation output must not overwrite an input")
            if temporary.exists():
                raise ValueError(f"validation path already exists: {temporary}")
            try:
                write_jsonl(temporary, rows)
                load_rows(temporary, "grpo")
                temporary.replace(args.output)
            finally:
                temporary.unlink(missing_ok=True)
            write_jsonl(skipped_path, skipped)
            print(json.dumps({"usable_groups": len(rows), "skipped_groups": len(skipped), "output": str(args.output)}))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
