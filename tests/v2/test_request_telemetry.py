from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
import subprocess
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.http import fetch_json
from norway_company_agent.network_policy import NetworkPolicy
from norway_company_agent.telemetry import RequestTelemetry


class RequestTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.controlled_temp = ROOT / ".release-gate-tmp"
        self.controlled_temp.mkdir(parents=True, exist_ok=True)

    def test_streaming_rows_classify_required_outcomes_without_bodies(self):
        with tempfile.TemporaryDirectory(dir=self.controlled_temp) as temporary:
            path = Path(temporary) / "telemetry.jsonl"
            sink = RequestTelemetry(run_id="telemetry-test", output=path)
            samples = [(200, None, "success"), (404, "HTTP 404", "not_found"), (429, "HTTP 429", "rate_limited"),
                       (0, "TimeoutError", "timeout"), (302, "HTTP 302", "redirect_rejected"),
                       (0, "source request budget exhausted", "source_budget_exhausted")]
            for index, (status, error, expected) in enumerate(samples, 1):
                row = sink.record(url="https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
                                  physical_request_number=None if expected == "source_budget_exhausted" else index,
                                  started_at="2026-10-12T00:00:00Z", completed_at="2026-10-12T00:00:01Z",
                                  duration_ms=1, status=status, bytes_received=3, error=error)
                self.assertEqual(row["classification"], expected)
                self.assertNotIn("body", row)
            saved = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(saved), 6)
            self.assertTrue(all(row["organisation_number"] == "923609016" for row in saved))

    def test_http_error_status_is_retained_for_telemetry(self):
        failure = urllib.error.HTTPError("https://data.brreg.no/enhetsregisteret/api/enheter/923609016", 429, "rate limited", {}, io.BytesIO(b"slow down"))
        with patch("urllib.request.urlopen", side_effect=failure):
            result = fetch_json("https://data.brreg.no/enhetsregisteret/api/enheter/923609016", attempts=1,
                                policy=NetworkPolicy.AUTHORIZED_PUBLIC_BRREG)
        self.assertEqual(result.status, 429)
        self.assertEqual(result.bytes_received, len(b"slow down"))
        self.assertEqual(result.error, "HTTP 429")

    def test_runner_refuses_ambiguous_multi_attempt_physical_counter(self):
        with tempfile.TemporaryDirectory(dir=self.controlled_temp) as temporary:
            root = Path(temporary)
            batch = root / "input.txt"; batch.write_text("923609016\n", encoding="utf-8")
            process = subprocess.run([
                sys.executable, "-S", "scripts/run_competition_batch.py", "--organisations", str(batch),
                "--bulk", str(root / "missing.csv"), "--output", str(root / "out.jsonl"),
                "--profiles-output", str(root / "profiles.jsonl"), "--report", str(root / "report.json"),
                "--run-id", "telemetry-refusal", "--request-telemetry", str(root / "telemetry.jsonl"),
                "--source-attempts", "2",
            ], cwd=ROOT, text=True, capture_output=True, timeout=10)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("requires --source-attempts 1", process.stderr + process.stdout)


if __name__ == "__main__":
    unittest.main()
