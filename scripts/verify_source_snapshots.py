#!/usr/bin/env python3
"""Verify a portable source-snapshot index without contacting sources."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from v0_kernel.storage import read_jsonl
from norway_company_agent.source_snapshots import verify_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", required=True, type=Path)
    args = parser.parse_args()
    index = args.snapshots / "index.jsonl"
    if not index.is_file():
        raise SystemExit("snapshot index is missing")
    entries = read_jsonl(index)
    bad = [entry.get("relative_path") for entry in entries if not verify_snapshot(args.snapshots, entry)]
    print(json.dumps({"entries": len(entries), "verified": len(entries) - len(bad), "invalid": bad}, indent=2))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
