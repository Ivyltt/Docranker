"""Read explicitly requested local credentials without executing shell syntax."""
from __future__ import annotations

import os
import shlex
from pathlib import Path

ALLOWED_KEYS = frozenset({"OPENROUTER_API_KEY", "HF_TOKEN"})


def read_env_file(path: str | Path) -> dict[str, str]:
    values = {}
    for line_number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.removeprefix("export ").partition("=")
        name = name.strip()
        if not separator or name not in ALLOWED_KEYS:
            raise ValueError(f"Unsupported environment entry at line {line_number}; expected a supported KEY=value")
        try:
            tokens = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise ValueError(f"Invalid quoting in environment file at line {line_number}") from None
        if len(tokens) > 1:
            raise ValueError(f"Unexpected whitespace in environment value at line {line_number}")
        values[name] = tokens[0] if tokens else ""
    return values


def load_env_file(path: str | Path) -> None:
    for key, value in read_env_file(path).items():
        if value:
            # An explicitly selected file is authoritative for this run.
            os.environ[key] = value
