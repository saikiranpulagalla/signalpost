"""Subprocess regression tests for the evaluator CLI's output lock and writer.

The fault hooks live only in these child processes.  The shipped CLI has no
test-mode flags: each child imports the real runner and changes its in-memory
binding before exercising the same ``write_jsonl`` path the CLI uses.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_competition_batch.py"
ORG = "923609016"


LOCK_HOLDER = r"""
import importlib.util
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("process_lock_runner", sys.argv[1])
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
with runner._output_lock(Path(sys.argv[2])):
    print("LOCKED", flush=True)
    time.sleep(float(sys.argv[3]))
"""


class ProcessLockingV2A4(unittest.TestCase):
    """Exercise OS locks and atomic commits across independent interpreters."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.children: list[subprocess.Popen[str]] = []
        self.addCleanup(self._stop_children)

    def _stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            if child.stdout is not None:
                child.stdout.close()
            if child.stderr is not None:
                child.stderr.close()

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        # The desktop sandbox's default system temp root rejects os.replace.
        # Keep child staging in the isolated test directory instead.
        environment["TEMP"] = str(self.root)
        environment["TMP"] = str(self.root)
        return environment

    def _run_code(self, code: str, *arguments: Path | str, timeout: int = 8) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-S", "-c", textwrap.dedent(code), str(RUNNER), *map(str, arguments)],
            cwd=ROOT, text=True, capture_output=True, env=self._environment(), timeout=timeout,
        )

    def _hold_lock(self, output: Path, seconds: float = 15) -> subprocess.Popen[str]:
        child = subprocess.Popen(
            [sys.executable, "-S", "-c", textwrap.dedent(LOCK_HOLDER), str(RUNNER), str(output), str(seconds)],
            cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._environment(),
        )
        self.children.append(child)
        line = child.stdout.readline().strip()
        if line != "LOCKED":
            self.fail(f"lock holder did not acquire its lock: {child.stderr.read()}")
        return child

    def _write_bulk(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode"])
            writer.writeheader()
            writer.writerow({"organisasjonsnummer": ORG, "navn": "Example AS", "organisasjonsform.kode": "AS"})

    def test_live_lock_rejects_same_path_and_does_not_block_other_paths(self):
        same = self.root / "same.jsonl"
        first = self._hold_lock(same)
        if first.poll() is not None:
            self.fail(f"first lock holder ended early: {first.stderr.read()}")
        attempted_at = time.monotonic()
        rejected = self._run_code(LOCK_HOLDER, same, "0")
        self.assertEqual(rejected.returncode, 1, f"lock contender unexpectedly succeeded after {time.monotonic() - attempted_at:.3f}s")
        self.assertIn("Concurrent writer rejected", rejected.stderr)

        other = self._hold_lock(self.root / "other.jsonl")
        self.assertIsNone(first.poll())
        self.assertIsNone(other.poll())

    def test_terminated_holder_and_leftover_lock_file_recover(self):
        output = self.root / "recovery.jsonl"
        holder = self._hold_lock(output)
        terminated_at = time.monotonic()
        holder.terminate()
        self.assertEqual(holder.wait(timeout=10), 1)
        # The exit value is platform-specific; prompt termination proves this
        # is not merely normal expiry of the holder's timed sleep.
        self.assertLess(time.monotonic() - terminated_at, 3)

        after_death = self._run_code(LOCK_HOLDER, output, "0")
        self.assertEqual(after_death.returncode, 0, after_death.stderr)
        self.assertIn("LOCKED", after_death.stdout)
        self.assertTrue(output.with_suffix(".jsonl.lock").exists())

        # A file is deliberately retained as the lock *anchor*.  With no
        # active OS byte-range/advisory lock it must not be treated as busy.
        leftover = self._run_code(LOCK_HOLDER, output, "0")
        self.assertEqual(leftover.returncode, 0, leftover.stderr)

    def test_replace_and_serialization_failures_preserve_committed_bytes(self):
        output = self.root / "committed.jsonl"
        replace_failure = self._run_code(
            r"""
            import importlib.util, sys
            from pathlib import Path
            import v0_kernel.storage as storage
            spec = importlib.util.spec_from_file_location("writer_runner", sys.argv[1])
            runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
            target = Path(sys.argv[2])
            runner.write_jsonl(target, [{"version": "old"}])
            previous = target.read_bytes()
            original = storage.os.replace
            storage.os.replace = lambda source, destination: (_ for _ in ()).throw(PermissionError("replace injected"))
            try:
                runner.write_jsonl(target, [{"version": "new"}])
            except PermissionError:
                pass
            else:
                raise SystemExit("replace fault was not raised")
            finally:
                storage.os.replace = original
            assert target.read_bytes() == previous
            assert not list(target.parent.glob(".committed.jsonl.*.tmp"))
            """,
            output,
        )
        self.assertEqual(replace_failure.returncode, 0, replace_failure.stderr)

        serialization_failure = self._run_code(
            r"""
            import importlib.util, sys
            from pathlib import Path
            spec = importlib.util.spec_from_file_location("writer_runner", sys.argv[1])
            runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
            target = Path(sys.argv[2])
            previous = target.read_bytes()
            try:
                runner.write_jsonl(target, [{"bad": object()}])
            except TypeError:
                pass
            else:
                raise SystemExit("serialization fault was not raised")
            assert target.read_bytes() == previous
            assert not list(target.parent.glob(".committed.jsonl.*.tmp"))
            """,
            output,
        )
        self.assertEqual(serialization_failure.returncode, 0, serialization_failure.stderr)

        recovered = self._run_code(
            r"""
            import importlib.util, json, sys
            from pathlib import Path
            spec = importlib.util.spec_from_file_location("writer_runner", sys.argv[1])
            runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
            target = Path(sys.argv[2])
            runner.write_jsonl(target, [{"version": "recovered"}])
            assert [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()] == [{"version": "recovered"}]
            """,
            output,
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)

    def test_profile_commit_without_final_envelope_is_not_successful_run(self):
        batch, bulk = self.root / "batch.txt", self.root / "bulk.csv"
        batch.write_text(ORG + "\n", encoding="utf-8")
        self._write_bulk(bulk)
        output, profiles, report = (self.root / "envelopes.jsonl", self.root / "profiles.jsonl", self.root / "report.json")
        interrupted = self._run_code(
            r"""
            import importlib.util, sys
            from pathlib import Path
            spec = importlib.util.spec_from_file_location("cli_runner", sys.argv[1])
            runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
            final_output = Path(sys.argv[4])
            original = runner.write_jsonl
            def fail_final(path, rows):
                if Path(path) == final_output:
                    raise OSError("injected final-envelope persistence failure")
                return original(path, rows)
            runner.write_jsonl = fail_final
            sys.argv = [str(sys.argv[1]), "--organisations", sys.argv[2], "--bulk", sys.argv[3],
                "--output", sys.argv[4], "--profiles-output", sys.argv[5], "--report", sys.argv[6],
                "--run-id", "process-lock-interrupted", "--expected-count", "1",
                "--modules", "registry,accounting_obligation", "--workers", "1"]
            runner.main()
            """,
            batch, bulk, output, profiles, report,
        )
        self.assertNotEqual(interrupted.returncode, 0)
        self.assertIn("injected final-envelope persistence failure", interrupted.stderr)
        self.assertTrue(profiles.exists())
        self.assertFalse(output.exists())
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["status"], "failed")
        self.assertEqual(len([json.loads(line) for line in profiles.read_text(encoding="utf-8").splitlines()]), 1)

        recovered = subprocess.run([
            sys.executable, "-S", "scripts/run_competition_batch.py", "--organisations", str(batch),
            "--bulk", str(bulk), "--output", str(output), "--profiles-output", str(profiles),
            "--report", str(report), "--run-id", "process-lock-recovery", "--expected-count", "1",
            "--modules", "registry,accounting_obligation", "--workers", "1",
        ], cwd=ROOT, text=True, capture_output=True, env=self._environment(), timeout=20)
        self.assertEqual(recovered.returncode, 0, recovered.stderr + recovered.stdout)
        self.assertTrue(output.exists())
        self.assertTrue(report.exists())


if __name__ == "__main__":
    unittest.main()
