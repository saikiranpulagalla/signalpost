"""V1d: offline-adversarial tests for genuine live-source preparation.

Synthetic reference digests are permitted *only in these tests*, never through
production CLI arguments. These do NOT constitute a real 100-company test.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.v1d_prepare_live_smoke import (Reference, prepare, read_verified_universe,
                                              check_digest, official_reference)
from scripts.v1d_live_source_smoke import summarize
from v0_kernel.contracts import parse_batch
from v0_kernel.registry import snapshot_checksum

ROOT = Path(__file__).resolve().parents[2]


def org(i: int) -> str:
    body = f"{90000000+i:08d}"
    weights = (3, 2, 7, 6, 5, 4, 3, 2)
    check = 11 - sum(int(d)*w for d, w in zip(body, weights)) % 11
    return body + str(0 if check == 11 else check) if check != 10 else org(i+1)


class TestV1dPreparation(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.p = Path(self.d.name)
        self.universe = self.p / 'companies.jsonl.gz'
        self.bulk = self.p / 'frozen.csv'
        self.batch = self.p / 'batch.jsonl'
        self.manifest = self.p / 'manifest.json'
        self.ids = [org(i) for i in range(8)]
        self.raw = b''.join(json.dumps({'organisation_number': x}).encode()+b'\n' for x in self.ids)
        self.universe.write_bytes(gzip.compress(self.raw, mtime=0))
        self.bulk.write_bytes(b'organisasjonsnummer;navn\n'+b'\n'.join(f'{x};REAL COMPANY {i}'.encode() for i,x in enumerate(self.ids))+b'\n')
        self.ref = Reference(snapshot_checksum(self.universe), hashlib.sha256(self.raw).hexdigest(),
                             snapshot_checksum(self.bulk), len(self.ids))

    def make(self, *, count=4, seed=42):
        return prepare(self.universe, self.bulk, self.batch, self.manifest,
                       reference=self.ref, count=count, seed=seed)

    def test_verified_smoke_batch(self):
        result = self.make()
        self.assertEqual(result['status'], 'VERIFIED_LOCAL_INPUTS_ONLY')
        self.assertEqual(result['batch_records'], 4)
        self.assertEqual(len(parse_batch(self.batch)), 4)
        self.assertEqual(snapshot_checksum(self.batch), result['batch_sha256'])
        self.assertFalse(result['official_evaluator_v2_schema_verified'])

    def test_stable_seed_and_reproducibility(self):
        self.make()
        one = self.batch.read_bytes()
        self.make()
        self.assertEqual(one, self.batch.read_bytes())
        self.make(seed=999)
        self.assertNotEqual(one, self.batch.read_bytes())

    def test_universe_archive_tampered(self):
        self.universe.write_bytes(self.universe.read_bytes()+b'junk')
        with self.assertRaisesRegex(ValueError, 'archive.*SHA-256'):
            self.make()

    def test_registry_changed(self):
        self.bulk.write_bytes(self.bulk.read_bytes()+b'9;MALICIOUS\n')
        with self.assertRaisesRegex(ValueError, 'registry.*SHA-256'):
            self.make()

    def test_uncompressed_digest_mismatch(self):
        wrong = Reference(self.ref.archive_sha256, '0'*64, self.ref.registry_sha256, 8)
        with self.assertRaisesRegex(ValueError, 'uncompressed universe hash'):
            prepare(self.universe,self.bulk,self.batch,self.manifest,reference=wrong)

    def test_wrong_expected_population(self):
        wrong = Reference(self.ref.archive_sha256,self.ref.content_sha256,self.ref.registry_sha256,9)
        with self.assertRaisesRegex(ValueError, 'row count mismatch'):
            prepare(self.universe,self.bulk,self.batch,self.manifest,reference=wrong)

    def test_invalid_or_duplicate_id_with_fresh_matching_digests(self):
        for changed in ([self.ids[0],self.ids[0]] , ['abcdefghi',self.ids[1]]):
            raw = b''.join(json.dumps({'organisation_number': x}).encode()+b'\n' for x in changed)
            self.universe.write_bytes(gzip.compress(raw,mtime=0))
            ref=Reference(snapshot_checksum(self.universe),hashlib.sha256(raw).hexdigest(),self.ref.registry_sha256,2)
            with self.assertRaisesRegex(ValueError, '(duplicate|invalid) org number'):
                prepare(self.universe,self.bulk,self.batch,self.manifest,reference=ref)

    def test_no_input_output_path_alias(self):
        with self.assertRaisesRegex(ValueError, 'distinct'):
            prepare(self.universe,self.bulk,self.universe,self.manifest,reference=self.ref)
        self.assertTrue(self.universe.is_file())

    def test_requested_count_bound(self):
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            self.make(count=100)
        with self.assertRaisesRegex(ValueError, 'Invalid smoke'):
            self.make(count=0)

    def test_content_must_be_gzip_even_when_hash_matches(self):
        self.universe.write_bytes(self.raw)
        r=Reference(snapshot_checksum(self.universe), hashlib.sha256(self.raw).hexdigest(),self.ref.registry_sha256,8)
        with self.assertRaisesRegex(ValueError, 'not gzip'):
            prepare(self.universe,self.bulk,self.batch,self.manifest,reference=r)

    def test_truncated_gzip_rejected(self):
        partial=self.universe.read_bytes()[:-8]
        self.universe.write_bytes(partial)
        r=Reference(snapshot_checksum(self.universe),self.ref.content_sha256,self.ref.registry_sha256,8)
        with self.assertRaises((ValueError, EOFError)):
            prepare(self.universe,self.bulk,self.batch,self.manifest,reference=r)

    def test_official_metadata_is_not_user_overridden(self):
        ref=official_reference()
        self.assertEqual(ref.rows, 411160)
        self.assertEqual(ref.content_sha256,
                         'b82d6a3e7231d1759a958c282bc4366b80ec2fab8095053d8ed7fa9cd01bc838')
        self.assertEqual(ref.archive_sha256,
                         '1c89710e5b01f8617e86d09fbdff4a52f2f8dbbba297e74f7164b5984f5a0384')

    def test_cli_no_network_without_explicit_consent(self):
        cmd=[sys.executable,'-S',str(ROOT/'scripts/v1d_live_source_smoke.py'),
             '--batch',str(self.batch),'--bulk',str(self.bulk),'--manifest',str(self.manifest),
             '--output',str(self.p/'output.jsonl'),'--report',str(self.p/'run.json'),
             '--summary',str(self.p/'summary.json')]
        result=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,2)
        self.assertIn('--run-live required',result.stdout)
        self.assertFalse((self.p/'output.jsonl').exists())


class TestV1dReport(unittest.TestCase):
    def setUp(self):
        self.orgs=[org(1),org(2)]
        self.rows=[{'organisation_number':x,'evidence':[{'source_class':'official_brreg_api'}],
                    'operations':{'official_fetch_ms':20},
                    'availability':{'roles':'available'}} for x in self.orgs]
        self.result={'source_mode':'LIVE_OFFICIAL_ENDPOINTS','run_status':'completed',
                     'validation':{'passed':True},'source_requests':2,'wall_clock_ms':60,
                     'unrecoverable_transport_timeout':False}
        self.manifest={'status':'VERIFIED_LOCAL_INPUTS_ONLY'}

    def summary(self):
        return summarize(self.result,self.rows,self.orgs,self.manifest,max_requests=100)

    def test_positive_live_summary_still_no_official_score(self):
        summary=self.summary()
        self.assertEqual(summary['status'],'LIVE_BRREG_FROZEN_SMOKE_PASS')
        self.assertFalse(summary['official_evaluator_v2_schema_verified'])
        self.assertIsNone(summary['official_score'])

    def test_unfrozen_diagnostic_is_not_labeled_frozen(self):
        self.manifest = {'status':'UNFROZEN_DIAGNOSTIC_LOCAL_INPUTS',
                         'registry_trust_mode':'UNFROZEN_USER_PINNED_DIAGNOSTIC'}
        summary = self.summary()
        self.assertEqual(summary['status'],'LIVE_BRREG_UNFROZEN_DIAGNOSTIC_PASS')
        self.assertFalse(summary['registry_frozen_reference_matched'])

    def test_fixture_transport_cannot_claim_live(self):
        self.result['source_mode']='SYNTHETIC_INJECTED_TRANSPORT'
        self.assertEqual(self.summary()['status'],'LIVE_BRREG_SMOKE_BLOCKED')

    def test_zero_requests_cannot_claim_live(self):
        self.result['source_requests']=0
        self.assertFalse(self.summary()['checks']['counted_nonzero_requests'])

    def test_stale_or_fake_evidence_cannot_claim_pass(self):
        for row in self.rows: row['evidence']=[]
        self.assertFalse(self.summary()['checks']['official_claim_evidence_present'])

    def test_failed_or_invalid_output_blocks(self):
        self.result['validation']={'passed':False}
        self.assertEqual(self.summary()['status'],'LIVE_BRREG_SMOKE_BLOCKED')
        self.result['validation']={'passed':True}
        self.rows.reverse()
        self.assertFalse(self.summary()['checks']['exact_input_membership_and_order'])


if __name__=='__main__':
    unittest.main()
