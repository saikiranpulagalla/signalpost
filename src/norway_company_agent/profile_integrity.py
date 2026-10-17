"""Verification of persisted observations, distinct from transport success.

An unkeyed digest supplied by the same file is not an authenticity anchor.
Frozen baselines require caller-supplied registry bytes; enriched replay
requires captured source bytes. Two immutable, previously reviewed legacy
fixtures are additionally pinned here. Those attest to retained observations,
not to raw response retention or cryptographic BRREG origin signatures.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .batch import canonical_organisation_number
from .official import normalize_entity, normalize_financials, normalize_roles, normalize_locations, normalize_group, normalize_financial_history
from .research_result import build_research_result

LEGACY_OBSERVATION_PINS = frozenset({
    "b5b9351d4566a10365bb5871a22d513d17c7b1cdfc3b328af40906d5b9b18f3c",
    "a95570bdd54395a893d0ef1613cc592d264cbb7ccacdb9d0dd2faa21d1a06e9c",
})
BASELINE_FIELDS = ("name", "legal_form", "employees", "bankrupt", "liquidating", "municipality", "municipality_number", "industry_code", "industry_label", "website", "latest_submitted_accounts")

def observation_key(path: Path) -> bytes:
    """Private local anchor, never embedded in exported profiles or releases."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(os.urandom(32))
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("Invalid private observation key")
    return key

def receipt(profile: dict, field: str, key: bytes) -> str:
    payload = {"organisation_number": profile["organisation_number"], "field": field,
               "observation": profile["evidence"][field]}
    return hmac.new(key, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()

def verify_receipt(profile: dict, field: str, key: bytes | None) -> bool:
    stored = profile.get("observation_receipts", {}).get(field)
    return bool(key and isinstance(stored, str) and hmac.compare_digest(stored, receipt(profile, field, key)))


def legacy_pin(data: bytes) -> bool:
    return hashlib.sha256(data).hexdigest() in LEGACY_OBSERVATION_PINS


def validate_metadata(profile: dict) -> None:
    org = canonical_organisation_number(profile.get("organisation_number"))
    records = profile.get("evidence")
    if not isinstance(records, dict):
        raise ValueError("Missing profile evidence")
    for field, record in records.items():
        if not isinstance(record, dict) or record.get("field") != field:
            raise ValueError("Invalid evidence record")
        if record.get("source_row_key") not in (None, org):
            raise ValueError("Evidence organisation attribution mismatch")
        parsed = urlsplit(str(record.get("source_url") or ""))
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Invalid evidence source URL")
        try:
            instant = datetime.fromisoformat(str(record.get("retrieved_at")).replace("Z", "+00:00"))
            if instant.utcoffset() is None or instant > datetime.now(timezone.utc):
                raise ValueError("Future or unzoned evidence timestamp")
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid evidence retrieval time") from exc
        digest = record.get("content_sha256")
        if (record.get("status") == "available" or digest is not None) and not re.fullmatch(r"[0-9a-fA-F]{64}", str(digest or "")):
            raise ValueError("Invalid evidence digest")
        if field in {"roles", "financials", "financial_history", "group", "registry_live"} and record.get("status") == "available":
            from .official import BRREG_ROLES, BRREG_ACCOUNTS, BRREG_ACCOUNT_YEARS, BRREG_GROUP, BRREG_ENTITY
            endpoints = {"roles": BRREG_ROLES, "financials": BRREG_ACCOUNTS, "financial_history": BRREG_ACCOUNT_YEARS, "group": BRREG_GROUP, "registry_live": BRREG_ENTITY}
            if record["source_url"] != endpoints[field].format(org=org):
                raise ValueError("Evidence URL does not identify the requested company")


def verify_baseline(profile: dict, authoritative: dict) -> None:
    if profile.get("organisation_number") != authoritative.get("organisation_number"):
        raise ValueError("Profile identity mismatch")
    if any(profile.get(field) != authoritative.get(field) for field in BASELINE_FIELDS):
        raise ValueError("Persisted registry fact contradicts authoritative snapshot")
    for field in ("registry", "accounting_obligation"):
        old = profile.get("evidence", {}).get(field, {})
        current = authoritative.get("evidence", {}).get(field, {})
        for key in ("value", "content_sha256", "source_url", "source_row_key", "status"):
            if old.get(key) != current.get(key):
                raise ValueError("Persisted baseline evidence contradicts authoritative snapshot")

def verify_live_baseline(profile: dict, key: bytes) -> None:
    if not verify_receipt(profile, "registry", key):
        raise ValueError("Unverifiable retained live identity")
    normalized = normalize_entity(profile["evidence"]["registry"]["value"])
    if normalized.get("organisation_number") != profile["organisation_number"]:
        raise ValueError("Retained live identity attribution mismatch")
    from .live_bootstrap import live_registry_projection
    for field, expected in live_registry_projection(normalized).items():
        if field in profile and profile[field] != expected:
            raise ValueError("Retained live baseline contradicts authenticated source")
    for field in ("registry_live", "accounting_obligation"):
        if not verify_receipt(profile, field, key):
            raise ValueError("Unverifiable retained live derived observation")
    if profile["evidence"]["registry_live"].get("value") != normalized:
        raise ValueError("Live normalized observation contradicts authenticated source")


def verify_source_record(profile: dict, field: str, source_dir: Path | None) -> bool:
    record = profile["evidence"][field]
    if source_dir is None:
        return False
    digest = record.get("content_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        return False
    file = source_dir / (digest + ".json")
    if not file.is_file():
        return False
    raw = file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Captured source bytes fail digest verification")
    if record.get("status") != "available":
        # Bytes alone do not attest to an HTTP outcome. E.g. a captured
        # successful payload must not be relabeled as checked absence.
        return False
    org = profile["organisation_number"]
    body = json.loads(raw)
    functions = {"financials": normalize_financials, "locations": normalize_locations,
                 "group": normalize_group, "financial_history": normalize_financial_history}
    if field in functions:
        expected = functions[field](body, org)
    elif field == "roles":
        if isinstance(body, dict) and body.get("organisasjonsnummer") not in (None, org):
            raise ValueError("Role source identity mismatch")
        expected = normalize_roles(body)
    elif field == "registry_live":
        expected = normalize_entity(body)
        if expected.get("organisation_number") != org:
            raise ValueError("Live identity source mismatch")
    else:
        return False
    if record.get("value") != expected:
        raise ValueError("Persisted normalized fact contradicts captured source")
    return True


def verify_claims(profile: dict, *, trusted_legacy: bool = False) -> None:
    if trusted_legacy:
        return  # Whole artifact is independently pinned, not self-attested.
    actual = profile.get("research_result")
    expected = build_research_result(profile)
    if isinstance(actual, dict) and "normalization_version" not in actual:
        # Narrow migration for authenticated pre-repair observations: the old
        # normalizer did not publish these five CSV-only fields. Existing
        # facts must still match exactly; final publication rebuilds all facts.
        expected.pop("normalization_version", None)
        introduced = {"registered_address", "registered_website", "entity_register_registration_date", "incorporation_date", "registry_latest_filed_year"}
        prior_ids = {claim.get("id") for claim in actual.get("claims", [])}
        expected["claims"] = [c for c in expected["claims"] if c["field"] not in introduced or c["id"] in prior_ids]
    if actual is not None and actual != expected:
        raise ValueError("Persisted claims contradict verified normalized observations")


def _history_payload(profile: dict) -> dict:
    return {"version": 1, "organisation_number": profile["organisation_number"],
            **{key: profile.get(key, default) for key, default in (
                ("changes", []), ("previous_evidence", {}), ("historical_observations", []))}}


def sign_history(profile: dict, key: bytes) -> None:
    """Call only after independently authenticating the previous/current inputs."""
    profile["history_authentication"] = hmac.new(key,
        json.dumps(_history_payload(profile), sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()


def verify_history(profile: dict, key: bytes | None) -> None:
    """History is a separate trust boundary, never authenticated by current claims."""
    changes = profile.get("changes", [])
    previous = profile.get("previous_evidence", {})
    historical = profile.get("historical_observations", [])
    if not changes and not previous and not historical:
        return
    if not isinstance(changes, list) or not isinstance(previous, dict) or not isinstance(historical, list):
        raise ValueError("Malformed retained history")
    expected = hmac.new(key or b"", json.dumps(_history_payload(profile), sort_keys=True,
        separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
    if not key or not hmac.compare_digest(str(profile.get("history_authentication", "")), expected):
        raise ValueError("Unverifiable retained history; authenticated prior inputs/key required")
    from .refresh import supported_change
    org = profile["organisation_number"]
    ids = set()
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("Invalid change event")
        field = change.get("field", "")
        module = field.split(".", 1)[0]
        old, new = change.get("previous_evidence"), change.get("current_evidence")
        for record in (old, new):
            validate_metadata({"organisation_number": org, "evidence": {module: record}})
        rebuilt = supported_change(org, field, old, new)
        if rebuilt is None or rebuilt != change or new != profile.get("evidence", {}).get(module):
            raise ValueError("Historical event contradicts authenticated observations")
        if rebuilt["id"] in ids:
            raise ValueError("Duplicate historical event")
        ids.add(rebuilt["id"])
    for module, record in previous.items():
        validate_metadata({"organisation_number": org, "evidence": {module: record}})
    seen = set()
    for row in historical:
        if not isinstance(row, dict) or row.get("organisation_number") != org:
            raise ValueError("Historical observation attribution mismatch")
        module, record = row.get("module"), row.get("evidence")
        validate_metadata({"organisation_number": org, "evidence": {module: record}})
        if record.get("status") != "available":
            raise ValueError("Last-known-good record was not available")
        identity = (module, record.get("content_sha256"), record.get("retrieved_at"))
        if identity in seen:
            raise ValueError("Duplicate historical observation")
        seen.add(identity)
