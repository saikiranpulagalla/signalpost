"""Bounded, deterministic interpretations of a supplied frozen Brreg CSV.

Do not confuse a file digest with proof of registry authenticity: callers must pin
its expected SHA256 through an independently obtained trusted manifest.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_SOURCE = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"
FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("legal_name", ("navn", "Navn")),
    ("legal_form", ("organisasjonsform.kode", "Organisasjonsform.kode")),
    ("industry_code", ("naeringskode1.kode", "Næringskode1.kode")),
    ("industry_description", ("naeringskode1.beskrivelse", "Næringskode1.beskrivelse")),
    ("registered_municipality", ("forretningsadresse.kommune", "Forretningsadresse.kommune")),
    ("registered_municipality_number", ("forretningsadresse.kommunenummer", "Forretningsadresse.kommunenummer")),
    ("last_submitted_accounts_year", ("sisteInnsendteAarsregnskap", "Siste innsendte årsregnskap")),
)
ORG_KEYS = ("organisasjonsnummer", "Organisasjonsnummer", "organisation_number")
MAX_FIELD_BYTES = 8 * 1024 * 1024


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key in row and isinstance(row[key], str) and row[key].strip():
            return row[key].strip()
    return None


def snapshot_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def pinned_private_copy(source: Path, expected_sha256: str | None = None):
    """Parse only the bytes copied and hashed, never reopen the mutable input.

    The temp directory is private.  Its snapshot path is not disclosed to callers
    outside this context.  The digest must still be anchored in a *trusted*
    manifest by the caller; hashing doesn't authenticate an arbitrary file.
    """
    with tempfile.TemporaryDirectory(prefix="signalpost-source-") as private:
        dest = Path(private) / "snapshot.bin"
        digest = hashlib.sha256()
        with source.open("rb") as src, dest.open("xb") as dst:
            while block := src.read(1024 * 1024):
                digest.update(block)
                dst.write(block)
            dst.flush()
            os.fsync(dst.fileno())
        result = digest.hexdigest()
        if expected_sha256 is not None and result != expected_sha256:
            raise ValueError("Pinned source SHA-256 mismatch")
        # A private immutable content copy prevents check-then-use races on the
        # original path. Detect even accidental modifications to the copy.
        os.chmod(dest, 0o400)
        try:
            yield dest, result
            if snapshot_checksum(dest) != result:
                raise ValueError("Private source snapshot modified during parse")
        finally:
            os.chmod(dest, 0o600)


def read_selected_rows(path: Path, wanted: set[str]) -> dict[str, dict[str, str]]:
    selected: dict[str, dict[str, str]] = {}
    with path.open("rb") as probe:
        compressed = probe.read(2) == b"\x1f\x8b"
    opener = gzip.open if compressed else open
    original_limit = csv.field_size_limit()
    try:
        # A recoverable 200KB garbage field must not kill all otherwise valid
        # records. This is still bounded; a larger field fails the source closed.
        csv.field_size_limit(MAX_FIELD_BYTES)
        with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(8192)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(handle, dialect=dialect)
            if not reader.fieldnames or not any(k in reader.fieldnames for k in ORG_KEYS):
                raise ValueError("Frozen bulk CSV missing organisation-number column")
            if len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValueError("Frozen bulk CSV has duplicate column names")
            for row in reader:
                if None in row:
                    raise ValueError("Frozen bulk CSV row has more columns than header")
                org = _pick(row, ORG_KEYS)
                if org in wanted:
                    if org in selected:
                        raise ValueError(f"Duplicate registry identity in CSV: {org}")
                    selected[org] = row
    finally:
        csv.field_size_limit(original_limit)
    return selected


def registry_facts(row: dict[str, str]) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = []
    for field, aliases in FIELDS:
        value = _pick(row, aliases)
        if value is None or len(value) > 2048 or "\x00" in value or any(ord(c) < 32 for c in value):
            continue
        if field == "legal_name" and not any(c.isalnum() for c in value):
            continue
        if field == "last_submitted_accounts_year" and (not re.fullmatch(r"[0-9]{4}", value) or
                                                        not 1900 <= int(value) <= datetime.now(timezone.utc).year + 1):
            continue
        facts.append((field, value))
    return facts


def row_hash(row: dict[str, str]) -> str:
    """Hash publishable source facts, so ignored CSV columns don't fake history."""
    canonical = {"organisation_number": _pick(row, ORG_KEYS), "facts": registry_facts(row)}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()
