"""Optional bounded retention of public-source bytes for future authorised runs."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def snapshot_permitted(url: str) -> bool:
    """Retain only low-personal-data public response families by default.

    Role responses can carry individual names and live entity responses can
    carry contact details, so their raw bytes remain evidence-by-reference.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "data.brreg.no":
        return False
    return "/regnskapsregisteret/regnskap/" in parsed.path or "/konsernstruktur/" in parsed.path


class SourceSnapshotStore:
    """Content-addressed, append-only public-response store.

    The caller is responsible for enabling this only for permitted public
    endpoints.  Existing captured evidence is never rewritten or deleted.
    """

    def __init__(self, root: Path | None, *, max_bytes: int = 25_000_000) -> None:
        if max_bytes < 0:
            raise ValueError("snapshot byte cap must be non-negative")
        self.root = root
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._stored = 0
        self._skipped = 0
        if root is not None:
            if root.exists():
                raise ValueError("source snapshot directory must be new and isolated")
            root.mkdir(parents=True)

    def retain(self, *, url: str, content_sha256: str | None, raw_bytes: bytes | None,
               metadata: dict[str, Any]) -> str | None:
        if self.root is None or raw_bytes is None or not content_sha256:
            return None
        if not snapshot_permitted(url):
            with self._lock:
                self._skipped += 1
            return None
        actual = hashlib.sha256(raw_bytes).hexdigest()
        if actual != content_sha256:
            raise ValueError("source snapshot digest differs from transport digest")
        with self._lock:
            if self._stored + len(raw_bytes) > self.max_bytes:
                self._skipped += 1
                return None
            target = self.root / f"{actual}.json"
            if not target.exists():
                target.write_bytes(raw_bytes)
                self._stored += len(raw_bytes)
            entry = {"content_sha256": actual, "relative_path": target.name, "source_url": url,
                     **metadata}
            with (self.root / "index.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            return target.name

    @property
    def metrics(self) -> dict[str, int]:
        return {"stored_bytes": self._stored, "skipped_for_budget": self._skipped}


def verify_snapshot(root: Path, entry: dict[str, Any]) -> bool:
    name = entry.get("relative_path")
    digest = entry.get("content_sha256")
    if not isinstance(name, str) or not isinstance(digest, str) or Path(name).name != name:
        return False
    file = root / name
    return file.is_file() and hashlib.sha256(file.read_bytes()).hexdigest() == digest
