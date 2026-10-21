#!/usr/bin/env python3
"""Offline evaluator-path scale exercise; never represents live capacity."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import subprocess
import sys
import time
from pathlib import Path


WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v0_kernel.storage import read_jsonl


def org_for(number: int) -> str | None:
    prefix = f"{number:08d}"
    check = 11 - sum(int(value) * weight for value, weight in zip(prefix, WEIGHTS)) % 11
    if check == 10:
        return None
    return prefix + str(0 if check == 11 else check)


def run_scale(root: Path, count: int, gzip_bulk: bool) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    orgs = []
    candidate = 50_000_000
    while len(orgs) < count:
        org = org_for(candidate)
        if org:
            orgs.append(org)
        candidate += 1
    batch, bulk = root / "batch.txt", root / ("bulk.csv.gz" if gzip_bulk else "bulk.csv")
    batch.write_text("\n".join(orgs) + "\n", encoding="utf-8")
    opener = gzip.open if gzip_bulk else open
    with opener(bulk, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode"])
        writer.writeheader()
        for index, org in enumerate(orgs):
            if (index + 1) % 10:
                writer.writerow({"organisasjonsnummer": org, "navn": f"SYNTHETIC {index} AS", "organisasjonsform.kode": "AS"})
    output, profiles, report = root / "envelopes.jsonl", root / "profiles.jsonl", root / "report.json"
    command = [sys.executable, "-S", "scripts/run_competition_batch.py", "--organisations", str(batch), "--bulk", str(bulk),
               "--profiles-output", str(profiles), "--output", str(output), "--report", str(report), "--run-id", f"v2a3-scale-{count}",
               "--expected-count", str(count), "--modules", "registry,accounting_obligation", "--workers", "8", "--checkpoint-every", "50"]
    command.extend(["--output-target", "starter_compatible"])
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    parsed = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    lines = read_jsonl(output) if output.exists() else []
    ordered = [line["organisation_number"] for line in lines] == orgs
    return {"count": count, "gzip_bulk": gzip_bulk, "exit_code": result.returncode, "elapsed_ms": elapsed_ms,
            "output_lines": len(lines), "ordered": ordered, "output_bytes": output.stat().st_size if output.exists() else 0,
            "report": parsed, "stderr_tail": result.stderr[-500:]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--counts", default="1,10,100,1000,1500")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    reports = []
    for count in [int(value) for value in args.counts.split(",")]:
        reports.append(run_scale(args.output_dir / f"plain-{count}", count, False))
    reports.append(run_scale(args.output_dir / "gzip-1500", 1500, True))
    document = {"source_mode": "SYNTHETIC_OFFLINE_ZERO_REQUESTS", "reports": reports}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(document, indent=2))
    return 0 if all(item["exit_code"] == 0 and item["output_lines"] == item["count"] and item["ordered"] for item in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
