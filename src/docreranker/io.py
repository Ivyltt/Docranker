"""Small, dependency-free data contracts and atomic JSONL writes."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Iterable


def read_jsonl(path: str | Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("expected an object")
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{path}:{line_no}: {exc}") from exc
            rows.append(row)
    return rows


def write_jsonl(path: str | Path, rows: Iterable[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def unique_index(rows: Iterable[dict], key: str) -> dict[str, dict]:
    index = {}
    for row in rows:
        value = str(row[key])
        if value in index:
            raise ValueError(f"duplicate {key}: {value}")
        index[value] = row
    return index


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def page_key(subset: str, doc_name: str, page_number: int) -> str:
    # JSON array encoding avoids collisions between delimiter-containing filenames.
    return json.dumps([subset, str(doc_name), int(page_number)], ensure_ascii=False, separators=(",", ":"))

