"""Verify the whole local transaction, not merely the envelope digest."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from v0_kernel.storage import read_jsonl


def verify_completion(*, report: Path, output: Path, profiles: Path, manifest: Path | None = None, candidate: dict | None = None) -> dict:
    manifest = manifest or report.with_suffix(report.suffix + ".completion.json")
    value = candidate if candidate is not None else json.loads(manifest.read_text(encoding="utf-8"))
    if value.get("status") != "completed":
        raise ValueError("Attempt is not completed")
    for key, file in (("report_sha256", report), ("envelope_sha256", output), ("profiles_sha256", profiles)):
        if value.get(key) != hashlib.sha256(file.read_bytes()).hexdigest():
            raise ValueError("Transaction artifact hash mismatch: " + key)
    result = json.loads(report.read_text(encoding="utf-8"))
    rows = read_jsonl(output)
    checkpoints = read_jsonl(profiles)
    orgs = [row["organisation_number"] for row in rows]
    if len(set(orgs)) != len(orgs) or orgs != [row["organisation_number"] for row in checkpoints]:
        raise ValueError("Transaction ordered membership mismatch")
    if len(rows) != value.get("envelope_count") or len(rows) != result.get("expected_count"):
        raise ValueError("Transaction count mismatch")
    if hashlib.sha256("\n".join(orgs).encode()).hexdigest() != value.get("expected_input_sha256"):
        raise ValueError("Transaction input digest mismatch")
    run_id = value.get("run_id")
    if result.get("run_id") != run_id or value.get("report_run_id") != run_id or any((row.get("run", {}).get("run_id") if result.get("output_target") == "starter_compatible" else row.get("run_id")) != run_id for row in rows):
        raise ValueError("Transaction run identity mismatch")
    if not result.get("validation", {}).get("passed") or result.get("worker_failures"):
        raise ValueError("Transaction contains failed validation or workers")
    plan = dict(result["execution_plan"])
    declared = plan.pop("sha256")
    digest = hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
    if digest != declared or digest != value.get("execution_plan_sha256"):
        raise ValueError("Transaction execution plan mismatch")
    if value.get("registry_snapshot_sha256") != result["registry"].get("registry_snapshot_sha256"):
        raise ValueError("Transaction registry fingerprint mismatch")
    for key in ("identity_mode", "output_target"):
        if value.get(key) != result.get(key):
            raise ValueError("Transaction configuration mismatch: " + key)
    if value.get("modules_sha256") != hashlib.sha256(",".join(result["modules"]).encode()).hexdigest():
        raise ValueError("Transaction module fingerprint mismatch")
    return value
