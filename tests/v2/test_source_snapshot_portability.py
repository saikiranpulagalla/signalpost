from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.source_snapshots import SourceSnapshotStore, verify_snapshot


class SourceSnapshotPortability(unittest.TestCase):
    def setUp(self):
        self.controlled = ROOT / ".release-gate-tmp"
        self.controlled.mkdir(exist_ok=True)

    def test_content_addressed_snapshot_verifies_without_network(self):
        with tempfile.TemporaryDirectory(dir=self.controlled) as temp:
            root = Path(temp) / "snapshots"
            store = SourceSnapshotStore(root, max_bytes=100)
            name = store.retain(url="https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
                                content_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                                raw_bytes=b"", metadata={"run_id": "fixture", "http_status": 200})
            self.assertEqual(name, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855.json")
            entry = json.loads((root / "index.jsonl").read_text(encoding="utf-8"))
            self.assertTrue(verify_snapshot(root, entry))
            (root / name).write_bytes(b"tampered")
            self.assertFalse(verify_snapshot(root, entry))

    def test_byte_budget_never_records_partial_snapshot(self):
        with tempfile.TemporaryDirectory(dir=self.controlled) as temp:
            store = SourceSnapshotStore(Path(temp) / "snapshots", max_bytes=2)
            self.assertIsNone(store.retain(url="https://data.brreg.no/regnskapsregisteret/regnskap/1", content_sha256="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                                           raw_bytes=b"abc", metadata={}))
            self.assertEqual(store.metrics, {"stored_bytes": 0, "skipped_for_budget": 1})

    def test_role_payload_is_not_raw_retained(self):
        with tempfile.TemporaryDirectory(dir=self.controlled) as temp:
            store = SourceSnapshotStore(Path(temp) / "snapshots", max_bytes=100)
            self.assertIsNone(store.retain(url="https://data.brreg.no/enhetsregisteret/api/enheter/923609016/roller",
                                           content_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                                           raw_bytes=b"", metadata={}))
            self.assertFalse((Path(temp) / "snapshots" / "index.jsonl").exists())
