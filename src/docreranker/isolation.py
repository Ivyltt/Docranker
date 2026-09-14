"""Compare content across train/evaluation namespaces without loading any model."""
from __future__ import annotations


def normalized_question(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("isolation audit requires a nonempty question")
    return " ".join(value.split()).casefold()


def normalized_document(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("isolation audit requires nonempty document names")
    # The official evaluation has its own subset namespace; train/eval document
    # identity therefore cannot be keyed by subset or by the full page_id.
    # Preserve directories and punctuation to avoid merging unrelated documents.
    return value.strip().casefold().removesuffix(".pdf")


def documents(row: dict) -> set[str]:
    names = row.get("doc_names")
    if not isinstance(names, list) or not names:
        raise ValueError(f"isolation audit requires doc_names: {row.get('query_id')}")
    return {normalized_document(name) for name in names}


def evaluation_overlap(training: list[dict], evaluation: list[dict]) -> list[dict]:
    """Return each overlapping training row once, with auditable reasons.

    Questions normalize whitespace and case; document names normalize case and
    a final .pdf suffix. This detects metadata overlap, not semantic duplicates
    or visually identical pages with unrelated names.
    """
    if not evaluation:
        raise ValueError("isolation audit requires nonempty evaluation queries")
    eval_questions, eval_documents = set(), set()
    for row in evaluation:
        eval_questions.add(normalized_question(row.get("query")))
        eval_documents.update(documents(row))
    records = []
    for row in training:
        question_overlap = normalized_question(row.get("query")) in eval_questions
        document_overlap = documents(row) & eval_documents
        if question_overlap or document_overlap:
            records.append({"query_id": row["query_id"], "subset": row.get("subset"),
                            "overlapping_documents": sorted(document_overlap),
                            "normalized_query_overlap": question_overlap})
    return records
