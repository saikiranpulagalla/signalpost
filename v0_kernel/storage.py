"""Atomic output to keep the previous whole-batch checkpoint parseable."""
from __future__ import annotations

import json
from .json_codec import loads as strict_loads, dumps as strict_dumps
import io
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(strict_dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        # Best effort: fsync directory after rename on POSIX.
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: Path, body: dict[str, Any]) -> None:
    atomic_jsonl(path, [body])


def _jsonl_values(lines: Iterable[bytes], *, encoding: str, require_object: bool) -> Iterator[Any]:
    """Decode physical LF records, never Unicode text line boundaries.

    Binary iteration keeps embedded U+2028/U+2029/U+0085 untouched. Remove
    only LF and its optional CR partner; CR-only framing is unsupported.
    Blank physical records containing JSON spaces/tabs remain ignored.
    """
    if encoding not in {"utf-8", "utf-8-sig"}:
        raise ValueError("JSONL requires strict UTF-8")
    for number, raw in enumerate(lines, 1):
        if raw.endswith(b"\n"):
            raw = raw[:-1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
        elif raw.endswith(b"\r"):
            raise ValueError(f"Unsupported CR-only JSONL terminator at record {number}")
        text = raw.decode(encoding if number == 1 else "utf-8")
        if not text.strip(" \t"):
            continue
        value = strict_loads(text)
        if require_object and not isinstance(value, dict):
            raise ValueError(f"JSONL row {number} is not an object")
        yield value


def iter_jsonl(path: str | Path, *, encoding: str = "utf-8", require_object: bool = False) -> Iterator[Any]:
    """Stream strict UTF-8 JSONL without rewriting bytes or source text."""
    with Path(path).open("rb") as handle:
        yield from _jsonl_values(handle, encoding=encoding, require_object=require_object)


def read_jsonl(path: str | Path, *, encoding: str = "utf-8", require_object: bool = False) -> list[Any]:
    return list(iter_jsonl(path, encoding=encoding, require_object=require_object))


def read_jsonl_bytes(raw: bytes, *, encoding: str = "utf-8", require_object: bool = False) -> list[Any]:
    """Parse already pinned bytes without reopening or changing their hash."""
    with io.BytesIO(raw) as handle:
        return list(_jsonl_values(handle, encoding=encoding, require_object=require_object))
