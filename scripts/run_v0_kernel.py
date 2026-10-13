#!/usr/bin/env python3
"""Run the offline V0 evaluator safety kernel with the starter's CLI arguments."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v0_kernel.runner import run


def main() -> int:
    p = argparse.ArgumentParser(description="Signalpost V0: offline frozen registry envelope runner")
    p.add_argument("--organisations", required=True, type=Path)
    p.add_argument("--bulk", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--report", required=True, type=Path)
    p.add_argument("--run-id", required=True)
    p.add_argument("--expected-count", type=int, default=None)
    p.add_argument("--previous-output", type=Path)
    p.add_argument("--checkpoint-every", type=int, default=25)
    p.add_argument("--fixture-mode", action="store_true", help="Synthetic/offline tests only; NEVER release eligible")
    p.add_argument("--expected-bulk-sha256", help="SHA-256 independently pinned from trusted source manifest")
    p.add_argument("--expected-previous-sha256", help="Prior output digest recorded before refresh")
    args = p.parse_args()
    try:
        result = run(**vars(args))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["execution_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
