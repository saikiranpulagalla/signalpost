"""Subprocess proof that offline evaluator paths cannot start company traffic."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_competition_batch.py"
EXPORTER = ROOT / "scripts" / "export_starter_compatible.py"
FIXTURE = ROOT / "tests" / "v2" / "fixtures" / "live-five-sanitized"


def valid_org(prefix: int) -> str:
    digits = f"{prefix:08d}"
    remainder = sum(int(digit) * weight for digit, weight in zip(digits, (3, 2, 7, 6, 5, 4, 3, 2))) % 11
    check = 0 if remainder == 0 else 11 - remainder
    if check == 10:
        raise ValueError("invalid prefix")
    return digits + str(check)


GUARD = r'''
import importlib.util, json, runpy, sys, urllib.request
from pathlib import Path
calls = []
def forbidden(*args, **kwargs):
    calls.append(str(args[0]) if args else "unknown")
    raise AssertionError("offline test attempted physical HTTP")
urllib.request.urlopen = forbidden
target, counter, *argv = sys.argv[1:]
try:
    if target.endswith("run_competition_batch.py"):
        spec = importlib.util.spec_from_file_location("guarded_runner", target)
        runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        sys.argv = [target, *argv]
        runner.main()
    else:
        sys.argv = [target, *argv]
        runpy.run_path(target, run_name="__main__")
except SystemExit as exc:
    code = int(exc.code) if isinstance(exc.code, int) else 1
else:
    code = 0
finally:
    Path(counter).write_text(json.dumps(calls), encoding="utf-8")
raise SystemExit(code)
'''


class OfflineNetworkPolicy(unittest.TestCase):
    def setUp(self):
        controlled = ROOT / ".release-gate-tmp"
        controlled.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=controlled)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.orgs = []
        candidate = 10_000_000
        while len(self.orgs) < 100:
            try:
                self.orgs.append(valid_org(candidate))
            except ValueError:
                pass
            candidate += 1
        self.batch, self.bulk = self.root / "batch.txt", self.root / "bulk.csv"
        self.batch.write_text("\n".join(self.orgs) + "\n", encoding="utf-8")
        with self.bulk.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode"])
            writer.writeheader()
            for index, org in enumerate(self.orgs):
                writer.writerow({"organisasjonsnummer": org, "navn": f"Offline {index}", "organisasjonsform.kode": "AS"})

    def _run_guarded(self, arguments: list[str], *, target: Path = RUNNER) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        counter = self.root / f"{target.stem}-{len(list(self.root.glob('*.calls.json')))}.calls.json"
        env = os.environ.copy(); env["TEMP"] = str(self.root); env["TMP"] = str(self.root)
        result = subprocess.run([sys.executable, "-S", "-c", textwrap.dedent(GUARD), str(target), str(counter), *arguments],
                                cwd=ROOT, text=True, capture_output=True, env=env, timeout=20)
        return result, json.loads(counter.read_text(encoding="utf-8"))

    def _runner_args(self, *, modules: str, count: int = 1, extra: list[str] | None = None) -> list[str]:
        selected_batch = self.root / f"batch-{count}.txt"
        selected_batch.write_text("\n".join(self.orgs[:count]) + "\n", encoding="utf-8")
        output, profiles, report = self.root / f"{modules.replace(',', '-')}-{count}.jsonl", self.root / f"profiles-{count}.jsonl", self.root / f"report-{count}.json"
        args = ["--organisations", str(selected_batch), "--bulk", str(self.bulk), "--output", str(output),
                "--profiles-output", str(profiles), "--report", str(report), "--run-id", f"offline-{count}",
                "--expected-count", str(count), "--modules", modules, "--workers", "1", "--source-attempts", "1"]
        if extra:
            args.extend(extra)
        return args

    def test_frozen_identity_only_and_synthetic_hundred_start_zero_http_requests(self):
        for count in (1, 100):
            result, calls = self._run_guarded(self._runner_args(modules="registry,accounting_obligation", count=count))
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(calls, [])
            report = json.loads((self.root / f"report-{count}.json").read_text(encoding="utf-8"))
            self.assertEqual(report["operations"]["requests"], 0)
            self.assertEqual(report["emitted_envelopes"], count)

    def test_optional_offline_enrichment_is_refused_before_output_or_http(self):
        args = self._runner_args(modules="registry,accounting_obligation,roles")
        result, calls = self._run_guarded(args)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertFalse((self.root / "report-1.json").exists())

    def test_budget_zero_is_truthful_and_never_starts_http(self):
        args = self._runner_args(modules="registry,accounting_obligation,roles", extra=["--max-source-requests", "0"])
        result, calls = self._run_guarded(args)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(calls, [])
        profile = json.loads((self.root / "profiles-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
        # A zero global cap is now an intentional pre-run omission, not
        # admitted work that unexpectedly remained not_fetched.
        self.assertNotIn("roles", profile["evidence"])
        report = json.loads((self.root / "report-1.json").read_text(encoding="utf-8"))
        self.assertEqual(report["execution_plan"]["admission"]["admitted_organisations"]["roles"], [])
        self.assertEqual(report["source_request_policy"]["source_requests_started"], 0)
        self.assertEqual(report["operations"]["requests"], 0)

    def test_saved_profile_export_and_resume_remain_offline(self):
        export = self.root / "saved-export.jsonl"
        result, calls = self._run_guarded([
            "--profiles", str(FIXTURE / "profiles.jsonl"), "--envelopes", str(FIXTURE / "envelopes.jsonl"),
            "--output", str(export), "--report", str(self.root / "saved-export.json"),
        ], target=EXPORTER)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(calls, [])
        initial, calls = self._run_guarded(self._runner_args(modules="registry,accounting_obligation"))
        self.assertEqual(initial.returncode, 0, initial.stderr + initial.stdout)
        resumed, calls = self._run_guarded(self._runner_args(modules="registry,accounting_obligation", extra=["--resume"]))
        self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
        self.assertEqual(calls, [])

    def test_current_data_without_authorization_is_refused_without_http(self):
        output, profiles, report = self.root / "live-out.jsonl", self.root / "live-profiles.jsonl", self.root / "live-report.json"
        result, calls = self._run_guarded([
            "--organisations", str(self.batch), "--live-bootstrap", "--output", str(output),
            "--profiles-output", str(profiles), "--report", str(report), "--run-id", "unauthorized-live",
            "--modules", "registry,accounting_obligation,registry_live", "--workers", "1",
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
