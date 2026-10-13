import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from scripts.v2a_website_fixture_smoke import run_fixture

ROOT=Path(__file__).resolve().parents[2]
FIX=ROOT/'tests/v2/fixtures/website-identity-SYNTHETIC.json'

class FixtureSmoke(unittest.TestCase):
    def test_complete_local_report(self):
        result=run_fixture(FIX)
        self.assertEqual(result['company_count'],2)
        self.assertEqual(result['website_claims_published'],0)
        self.assertIsNone(result['official_score'])
        self.assertEqual(result['companies'][0]['identity']['status'],'group_review')
        self.assertEqual(result['companies'][1]['identity']['status'],'exact_page_identity')
        self.assertTrue(all(row['publishable'] is False for row in result['companies']))

    def test_cli_never_overwrites_fixture(self):
        proc=subprocess.run([sys.executable,'-S','scripts/v2a_website_fixture_smoke.py',
                             '--fixture',str(FIX),'--output',str(FIX)],cwd=ROOT,capture_output=True)
        self.assertNotEqual(proc.returncode,0)
        self.assertTrue(FIX.exists())

    def test_cli_valid_output(self):
        with tempfile.TemporaryDirectory() as t:
            dest=Path(t)/'result.json'
            proc=subprocess.run([sys.executable,'-S','scripts/v2a_website_fixture_smoke.py',
                                 '--fixture',str(FIX),'--output',str(dest)],cwd=ROOT,capture_output=True)
            self.assertEqual(proc.returncode,0,proc.stderr.decode())
            self.assertEqual(json.loads(dest.read_text())['website_claims_published'],0)

    def test_malformed_fixture_fails(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'bad.json';p.write_text('{"100":{"legal_name":"A","registry_body":{}}}')
            with self.assertRaises(ValueError):run_fixture(p)

if __name__=='__main__':unittest.main()
