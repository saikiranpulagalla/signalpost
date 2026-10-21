from __future__ import annotations

import math
import json
import re
import threading
import time
from datetime import date, datetime
from typing import Any, Callable

from .evidence import evidence
from .http import FetchResult, fetch_json

ACCOUNTING_OBLIGATION_SOURCE = "https://www.brreg.no/en/submission-of-annual-accounts/reporting-obligations-to-the-register-of-company-accounts/who-has-an-accounting-obligation/"
ACCOUNTING_RULESET_VERSION = "brreg_accounting_obligation_rules_2024-08-09_v1"
ALWAYS_ACCOUNTING_OBLIGED_FORMS = {"AS", "ASA", "BRL", "BBL", "STI", "SF", "VPFO"}
THRESHOLD_OR_ACTIVITY_FORMS = {"ENK", "ANS", "DA", "SA", "FLI", "ESEK", "NUF", "UTLA", "ORGL", "SAM", "SPA", "KS", "BO"}

BRREG_ENTITY = "https://data.brreg.no/enhetsregisteret/api/enheter/{org}"
BRREG_ROLES = BRREG_ENTITY + "/roller"
BRREG_GROUP = "https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{org}"
BRREG_SUBUNITS = "https://data.brreg.no/enhetsregisteret/api/underenheter?overordnetEnhet={org}&size=1000"
BRREG_ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap/{org}"
BRREG_ACCOUNT_YEARS = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/aar"
BRREG_ACCOUNT_PDF = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/{year}"

_history_lock = threading.Lock()
_history_last_request = 0.0


def accounting_obligation_assessment(profile: dict[str, Any]) -> dict[str, Any]:
    """Classify the rule path without inventing a definitive exemption.

    Brreg's public rule is categorical for some forms and threshold/activity dependent for others.
    Registry employee counts are not treated as equivalent to statutory man-years or asset/revenue tests.
    """
    legal_form = str(profile.get("legal_form") or "").upper()
    latest = profile.get("latest_submitted_accounts")
    if latest:
        classification = "filing_observed"
        reason = "The registry snapshot reports a latest submitted annual-account year."
    elif legal_form in ALWAYS_ACCOUNTING_OBLIGED_FORMS:
        classification = "required_by_legal_form"
        reason = f"Brreg lists organisation form {legal_form} in an always-obliged category."
    elif legal_form in THRESHOLD_OR_ACTIVITY_FORMS:
        classification = "threshold_or_activity_dependent"
        reason = "Obligation depends on statutory size, partner, activity, tax or supervision tests not fully present in the open entity row."
    else:
        classification = "special_rule_or_review_required"
        reason = "The open entity row is insufficient for a definitive accounting-obligation decision."
    ruleset_hash = __import__("hashlib").sha256(ACCOUNTING_RULESET_VERSION.encode()).hexdigest()
    return evidence(
        "accounting_obligation",
        "available",
        "official_rule_interpretation",
        ACCOUNTING_OBLIGATION_SOURCE,
        value={
            "classification": classification,
            "legal_form": legal_form or None,
            "latest_submitted_accounts": latest or None,
            "reason": reason,
            "ruleset_version": ACCOUNTING_RULESET_VERSION,
        },
        as_of="2024-08-09",
        content_sha256=ruleset_hash,
        source_row_key=str(profile.get("organisation_number") or "") or None,
    )


def _get(value: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _financial_amount(value: Any) -> int | float | None:
    """Return only a finite source amount; absence is not zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _financial_period(value: Any) -> dict[str, str] | None:
    """Validate the OpenAPI Tidsperiode dates before treating them as a fact."""
    if not isinstance(value, dict):
        return None
    start, end = value.get("fraDato"), value.get("tilDato")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        if date.fromisoformat(start) > date.fromisoformat(end):
            return None
    except ValueError:
        return None
    # Retain the source's OpenAPI field names.  Consumers may add display
    # aliases, but source-period validation must not rewrite provenance.
    return {"fraDato": start, "tilDato": end}


def normalize_financials(body: Any, org: str | None = None) -> dict[str, Any]:
    """Normalize documented Regnskapsregisteret v3 observations conservatively.

    ``SELSKAP`` statements are exact-legal-entity observations. ``KONSERN``
    records are preserved with an explicit consolidated scope so callers cannot
    accidentally describe group figures as figures for the requested company.
    Invalid records are retained only as diagnostics, never as claims.
    """
    source_records = body if isinstance(body, list) else []
    normalized: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in source_records:
        if not isinstance(item, dict):
            rejected.append({"reason": "invalid_record"})
            continue
        # V3 documents the nested ``virksomhet`` object.  V1's saved source
        # fixtures predate that shape and retain the same identity at the
        # record root; accept that legacy representation only when it still
        # passes the identical nine-digit and caller-org checks below.
        source_org = _get(item, "virksomhet", "organisasjonsnummer") or item.get("organisasjonsnummer")
        if not isinstance(source_org, str) or not re.fullmatch(r"\d{9}", source_org):
            rejected.append({"record_id": item.get("id"), "reason": "missing_or_invalid_organisation_number"})
            continue
        if org is not None and source_org != org:
            rejected.append({"record_id": item.get("id"), "reason": "organisation_number_mismatch"})
            continue
        account_type = item.get("regnskapstype")
        if account_type not in {"SELSKAP", "KONSERN"}:
            rejected.append({"record_id": item.get("id"), "reason": "unsupported_regnskapstype"})
            continue
        period = _financial_period(item.get("regnskapsperiode"))
        if period is None:
            rejected.append({"record_id": item.get("id"), "reason": "invalid_reporting_period"})
            continue
        currency = item.get("valuta")
        if not isinstance(currency, str) or not currency.strip():
            rejected.append({"record_id": item.get("id"), "reason": "missing_currency"})
            continue
        normalized.append({
            "record_id": item.get("id"),
            "organisation_number": source_org,
            "account_type": account_type,
            "scope": "exact_legal_entity" if account_type == "SELSKAP" else "consolidated_group",
            "period": period,
            "currency": currency,
            "revenue": _financial_amount(_get(item, "resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter")),
            "operating_result": _financial_amount(_get(item, "resultatregnskapResultat", "driftsresultat", "driftsresultat")),
            "profit_before_tax": _financial_amount(_get(item, "resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad")),
            "annual_result": _financial_amount(_get(item, "resultatregnskapResultat", "aarsresultat")),
            "assets": _financial_amount(_get(item, "eiendeler", "sumEiendeler")),
            "equity": _financial_amount(_get(item, "egenkapitalGjeld", "egenkapital", "sumEgenkapital")),
            "debt": _financial_amount(_get(item, "egenkapitalGjeld", "gjeldOversikt", "sumGjeld")),
        })
    return {"records": normalized[:3], "rejected_records": rejected}


def normalize_financial_history(body: Any, org: str) -> dict[str, Any]:
    years = sorted({str(year) for year in body if str(year).isdigit()}) if isinstance(body, list) else []
    return {
        "years": years,
        "pdfs": [
            {"year": year, "url": BRREG_ACCOUNT_PDF.format(org=org, year=year)}
            for year in reversed(years)
        ],
    }


def _reserve_history_slot(clock: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep) -> None:
    """Reserve starts at least 2.1 seconds apart without serializing response time."""
    global _history_last_request
    with _history_lock:
        delay = 2.1 - (clock() - _history_last_request)
        if delay > 0:
            sleeper(delay)
        _history_last_request = clock()


def _fetch_history(url: str) -> FetchResult:
    """Keep this endpoint below its observed 30-request-starts/minute allowance."""
    _reserve_history_slot()
    return fetch_json(url)


def normalize_roles(body: Any) -> dict[str, Any]:
    roles = []
    for group in body.get("rollegrupper", []) if isinstance(body, dict) else []:
        changed = group.get("sistEndret")
        for item in group.get("roller", []):
            person = item.get("person") or {}
            name = person.get("navn") or {}
            entity = item.get("enhet") or {}
            display_name = " ".join(filter(None, [name.get("fornavn"), name.get("mellomnavn"), name.get("etternavn")])) or entity.get("navn")
            roles.append({
                "name": display_name or None,
                "organisation_number": entity.get("organisasjonsnummer"),
                "role_code": _get(item, "type", "kode"),
                "role": _get(item, "type", "beskrivelse"),
                "group_code": _get(group, "type", "kode"),
                "group": _get(group, "type", "beskrivelse"),
                "last_changed": changed,
                "inactive": bool(item.get("avregistrert")),
            })
    return {"roles": roles}


def normalize_locations(body: Any, org: str | None = None) -> dict[str, Any]:
    from .batch import canonical_organisation_number
    rows = _get(body, "_embedded", "underenheter") or []
    valid = []
    rejected = []
    for item in rows:
        if not isinstance(item, dict):
            rejected.append("invalid_record")
            continue
        parent = item.get("overordnetEnhet")
        try:
            canonical_organisation_number(item.get("organisasjonsnummer"))
            canonical_organisation_number(parent)
        except ValueError:
            rejected.append("invalid_or_missing_identity")
            continue
        if org is None or parent != org:
            rejected.append("parent_identity_mismatch")
            continue
        valid.append(item)
    incomplete = bool(_get(body, "_links", "next")) or (_get(body, "page", "totalPages") or 1) > 1
    return {"complete": not incomplete, "rejected_records": rejected, "locations": [{
        "organisation_number": item.get("organisasjonsnummer"),
        "parent_organisation_number": item.get("overordnetEnhet"),
        "name": item.get("navn"),
        "address": item.get("beliggenhetsadresse") or item.get("postadresse"),
        "industry": item.get("naeringskode1"),
        "employees": item.get("antallAnsatte"),
    } for item in valid]}


def normalize_group(body: Any, org: str) -> dict[str, Any]:
    """Keep only explicitly identified corporate relations from BRREG.

    The endpoint's detailed shape has varied over time.  Retaining selected
    records verbatim would invite accidental parent/subsidiary inference, so
    preserve only relation entries that name an organisation number and a
    declared relation label.
    """
    relations = []
    # Current BRREG OpenAPI documents a root entity with recursively nested
    # ``children``.  Each child carries its own parent number and a declared
    # ``knytningsform``; preserve those source facts rather than inferring
    # parent/subsidiary direction from a company name or tree depth.
    def visit(items: Any) -> None:
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            related = item.get("organisasjonsnummer") or item.get("enhetsnummer")
            relation = item.get("knytningsform") or item.get("relasjon") or item.get("type") or item.get("rolle")
            if isinstance(related, str) and related.isdigit() and len(related) == 9 and related != org and isinstance(relation, (str, dict)):
                relations.append({
                    "organisation_number": related, "name": item.get("navn"),
                    "declared_relationship": relation,
                    "parent_organisation_number": item.get("parentOrganisasjonsnummer"),
                    "parent_name": item.get("parentNavn"), "effective_date": item.get("dato"),
                    "basis": item.get("grunnlag"),
                })
            visit(item.get("children"))

    if isinstance(body, dict) and isinstance(body.get("children"), list):
        visit(body["children"])
    if isinstance(body, list):
        candidates = body
    elif isinstance(body, dict):
        candidates = body.get("konsern") or body.get("relasjoner") or []
    else:
        candidates = []
    for item in candidates if isinstance(candidates, list) else []:
        if not isinstance(item, dict):
            continue
        related = item.get("organisasjonsnummer") or item.get("enhetsnummer")
        relation = item.get("relasjon") or item.get("type") or item.get("rolle")
        if isinstance(related, str) and related.isdigit() and len(related) == 9 and related != org and isinstance(relation, (str, dict)):
            relations.append({"organisation_number": related, "name": item.get("navn"), "declared_relationship": relation})
    return {"relations": relations}


def registered_name_history(body: dict[str, Any], observed_at: str | None) -> list[dict[str, str]]:
    """Literal former-name intervals, not observed changes or current aliases.

    The retained public representation uses unzoned wall-clock timestamps.
    Preserve those strings; do not invent UTC/effective-time semantics. Withhold
    missing, invalid, conflicting or not-demonstrably-past intervals. In
    particular an end on the observation's UTC calendar date is conservative
    unknown because the source interval has no timezone.
    """
    from .batch import canonical_organisation_number
    try:
        org = canonical_organisation_number(body.get("organisasjonsnummer"))
        observed = datetime.fromisoformat((observed_at or "").replace("Z", "+00:00"))
        if observed.utcoffset() is None:
            return []
    except (ValueError, TypeError):
        return []
    rows = body.get("historiskeNavn")
    if not isinstance(rows, list) or len(rows) > 1000:
        return []
    values: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("organisasjonsnummer", org) != org:
            continue
        name, start, end = row.get("navn"), row.get("fraDato"), row.get("tilDato")
        if not isinstance(name, str) or not name.strip() or len(name.encode("utf-8")) > 4096:
            continue
        if not all(isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", v) for v in (start, end)):
            continue
        try:
            a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
            if a >= b or b.date() >= observed.date():
                continue
        except ValueError:
            continue
        values[(start, end, name)] = {"name": name, "valid_from": start, "valid_to": end,
                                     "time_basis": "source wall-clock timestamps; timezone not supplied"}
    return [values[key] for key in sorted(values)]


def normalize_entity(body: Any, *, declarations: bool = False, history: bool = False,
                     observed_at: str | None = None) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    result = {
        "organisation_number": body.get("organisasjonsnummer"),
        "name": body.get("navn"),
        "legal_form": _get(body, "organisasjonsform", "kode"),
        "employees": body.get("antallAnsatte"),
        "bankrupt": body.get("konkurs"),
        "liquidating": body.get("underAvvikling"),
        "website": body.get("hjemmeside"),
        "industry": body.get("naeringskode1"),
        "business_address": body.get("forretningsadresse"),
        "postal_address": body.get("postadresse"),
        "latest_submitted_accounts": body.get("sisteInnsendteAarsregnskap"),
    }
    if declarations:
        from .sampling import registered_declaration
        for source, field in (("aktivitet", "registered_activity"), ("vedtektsfestetFormaal", "registered_purpose")):
            raw = body.get(source)
            if isinstance(raw, list):
                raw = json.dumps(raw, ensure_ascii=False)
            value = registered_declaration(raw)
            if value is not None and len(json.dumps(value, ensure_ascii=False).encode("utf-8")) <= 1_000_000:
                result[field] = list(dict.fromkeys(value)) if isinstance(value, list) else value
                result["declaration_projection_version"] = 1
    if history:
        names = registered_name_history(body, observed_at)
        if names:
            result["registered_name_history"] = names
            result["name_history_projection_version"] = 1
    return result


def _classified(field: str, source_type: str, result: FetchResult, value: Any = None, *, org: str | None = None) -> dict[str, Any]:
    if result.status == 200 and not result.error:
        return evidence(field, "available", source_type, result.url, value=result.body if value is None else value, content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, effective_at=result.effective_at, source_row_key=org)
    if result.status in {404, 410}:
        return evidence(field, "not_found", source_type, result.url, note=result.error, content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, effective_at=result.effective_at, source_row_key=org)
    if result.status in {401, 403}:
        return evidence(field, "blocked", source_type, result.url, note=result.error,
                        content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, source_row_key=org)
    if result.error in {"source request budget exhausted", "source family budget exhausted", "execution deadline reserve reached", "source service cooldown", "transport service stopped", "network policy offline", "network policy rejected URL"}:
        # This is a local execution limit, not evidence that the source
        # failed or that the company has no data.  The evaluator adapter has
        # no documented equivalent and therefore refuses to publish it.
        return evidence(field, "not_fetched", source_type, result.url, note=result.error,
                        retrieved_at=result.retrieved_at, effective_at=result.effective_at, source_row_key=org)
    return evidence(field, "source_error", source_type, result.url, note=result.error, content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, effective_at=result.effective_at, source_row_key=org)


def fetch_official_modules(org: str, modules: set[str], fetcher: Callable[[str], FetchResult] = fetch_json,
                           module_order: list[str] | None = None) -> tuple[dict[str, Any], list[FetchResult]]:
    records: dict[str, Any] = {}
    metrics: list[FetchResult] = []
    endpoints = {
        "registry_live": (BRREG_ENTITY.format(org=org), "official_registry_live"),
        "financials": (BRREG_ACCOUNTS.format(org=org), "official_annual_accounts"),
        "financial_history": (BRREG_ACCOUNT_YEARS.format(org=org), "official_annual_account_copies"),
        "roles": (BRREG_ROLES.format(org=org), "official_roles"),
        "group": (BRREG_GROUP.format(org=org), "official_group_structure"),
        "locations": (BRREG_SUBUNITS.format(org=org), "official_subunits"),
    }
    ordered = module_order or list(endpoints)
    for module in ordered:
        if module not in endpoints:
            continue
        url, source_type = endpoints[module]
        if module not in modules:
            continue
        result = _fetch_history(url) if module == "financial_history" and fetcher is fetch_json else fetcher(url)
        metrics.append(result)
        normalized = None
        if result.status == 200 and ((module == "registry_live" and (not isinstance(result.body, dict) or result.body.get("organisasjonsnummer") != org)) or (module in {"roles", "group"} and isinstance(result.body, dict) and result.body.get("organisasjonsnummer") not in (None, org))):
            records[module] = evidence(module, "source_error", source_type, result.url,
                note="Returned source organisation conflicts with requested identity",
                content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, source_row_key=org)
            continue
        if result.status == 200 and not result.error:
            normalized = normalize_entity(result.body, declarations=True, history=True, observed_at=result.retrieved_at) if module == "registry_live" else normalize_financials(result.body, org) if module == "financials" else normalize_financial_history(result.body, org) if module == "financial_history" else normalize_roles(result.body) if module == "roles" else normalize_locations(result.body, org) if module == "locations" else normalize_group(result.body, org) if module == "group" else result.body
        records[module] = _classified(module, source_type, result, value=normalized, org=org)
        if module == "registry_live" and isinstance(normalized, dict) and normalized.get("declaration_projection_version") == 1:
            records[module]["extraction_method"]["version"] = "signalpost-live-declarations/2026-10-20"
        if module == "registry_live" and isinstance(normalized, dict) and normalized.get("name_history_projection_version") == 1:
            records[module]["extraction_method"]["version"] = "signalpost-registry-history/2026-10-20"
        records[module]["http_status"] = result.status or None
        if module == "locations" and isinstance(normalized, dict):
            records[module]["completeness"] = "complete" if normalized["complete"] else "partial"
            if normalized["rejected_records"]:
                records[module]["note"] = "Rejected subunit records: " + ", ".join(normalized["rejected_records"])
    return records, metrics
