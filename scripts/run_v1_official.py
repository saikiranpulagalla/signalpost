#!/usr/bin/env python3
"""Run bounded Signalpost V1a official BRREG research (offline fixtures or live)."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v1_official.runner import run


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--organisations", type=Path, required=True)
    p.add_argument("--bulk", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--expected-count", type=int)
    p.add_argument("--expected-bulk-sha256")
    p.add_argument("--fixture-mode", action="store_true")
    p.add_argument("--fixture-responses", type=Path)
    p.add_argument("--allow-live", action="store_true")
    p.add_argument("--max-requests", type=int, default=500)
    p.add_argument("--max-seconds", type=float, default=180)
    p.add_argument("--checkpoint-every", type=int, default=10)
    p.add_argument("--previous-output", type=Path, help="Previously trusted V1 JSONL for refresh")
    p.add_argument("--expected-previous-sha256", help="Independent SHA-256 of prior JSONL")
    p.add_argument("--evidence-cutoff", help="Evaluator cutoff, ISO-8601 with timezone (optional)")
    args = p.parse_args()
    try:
        report = run(**vars(args))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["run_status"] == "completed" else 1

if __name__ == "__main__":
    raise SystemExit(main())
