"""Use the same ColQwen index settings for pilot or full training retrieval."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from docreranker.io import read_jsonl, write_jsonl
from docreranker.isolation import evaluation_overlap

PROJECT = Path(__file__).resolve().parents[1]


def prepare_queries(args):
    """Keep the original split and write the exact isolated retrieval input.

    The already queued full-training command uses the canonical split path.
    Enforce evaluation isolation there even without an added Slurm argument;
    pilot/custom commands only opt in with --evaluation-queries.
    """
    evaluation = args.evaluation_queries
    if args.training and args.queries.resolve() == (PROJECT / "data/splits/train_queries.jsonl").resolve():
        evaluation = evaluation or PROJECT / "data/evaluation/queries.jsonl"
    if evaluation is None:
        return args.queries, None
    if not args.training:
        raise ValueError("--evaluation-queries is only valid for training retrieval")
    from hashlib import sha256
    source_rows, evaluation_rows = read_jsonl(args.queries), read_jsonl(evaluation)
    if any(row.get("split") not in {"evaluation", "test"} for row in evaluation_rows):
        raise ValueError("evaluation isolation requires the independent evaluation split")
    overlaps = evaluation_overlap(source_rows, evaluation_rows)
    excluded = {row["query_id"] for row in overlaps}
    safe_rows = [row for row in source_rows if row["query_id"] not in excluded]
    if not safe_rows:
        raise ValueError("evaluation isolation removed every training query")
    filtered = args.output.with_suffix(".queries.jsonl")
    if filtered.resolve() in {args.queries.resolve(), Path(evaluation).resolve()}:
        raise ValueError("filtered query output must not overwrite source metadata")
    write_jsonl(filtered, safe_rows)
    report = {"status": "filtered", "source_queries": str(args.queries.resolve()),
              "evaluation_queries": str(Path(evaluation).resolve()),
              "filtered_queries": str(filtered.resolve()), "original_queries": len(source_rows),
              "excluded_queries": len(overlaps), "remaining_queries": len(safe_rows),
              "records": overlaps,
              "normalization": {"query": "whitespace collapse and casefold",
                                "document": "strip, casefold, remove final .pdf; ignore subset namespace"},
              "input_sha256": {str(Path(path).resolve()): sha256(Path(path).read_bytes()).hexdigest()
                               for path in (args.queries, evaluation, filtered)}}
    report_path = args.output.with_suffix(".isolation.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return filtered, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--index", type=Path, default=Path("data/index/train"))
    parser.add_argument("--training", action="store_true")
    parser.add_argument("--evaluation-queries", type=Path,
                        help="exclude overlapping questions/documents; mandatory automatically for the canonical full train split")
    args = parser.parse_args(argv)
    queries, isolation_report = prepare_queries(args)
    command = [sys.executable, "-m", "docreranker.retrieval"]
    subprocess.run([*command, "index", "--pages", str(args.pages), "--output", str(args.index),
                    "--model", "models/colqwen2", "--batch-size", "4"], check=True)
    search = [*command, "search", "--queries", str(queries), "--index", str(args.index),
              "--output", str(args.output), "--top-k", "5"]
    if args.training:
        search.append("--training")
    subprocess.run(search, check=True)
    print(json.dumps({"status": "completed", "output": str(args.output),
                      "evaluation_isolation": isolation_report}), flush=True)


if __name__ == "__main__":
    main()
