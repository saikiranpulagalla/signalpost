"""Focused functional coverage for connected official research, synthesis and viewer."""
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
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.evidence import evidence
from norway_company_agent.research_result import attach_research_result
from norway_company_agent.official import normalize_group
from norway_company_agent.evaluator_adapter import OfficialSchemaUnavailableError, adapt_research_result

ORG = "923609016"


class ConnectedResearchBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_claims_and_synthesis_are_evidence_linked(self):
        profile = {
            "organisation_number": ORG, "name": "Example AS", "legal_form": "AS",
            "industry_code": "62.010", "industry_label": "Computer programming", "employees": 7,
            "bankrupt": False, "liquidating": False,
            "evidence": {
                "registry": evidence("registry", "available", "official_registry_bulk", "https://data.brreg.no/", value={}),
                "roles": evidence("roles", "available", "official_roles", "https://data.brreg.no/", value={"roles": [{"name": "Test Person", "role": "Director", "group": "Board"}]}),
                "locations": evidence("locations", "available", "official_subunits", "https://data.brreg.no/", value={"locations": [{"organisation_number": "910608193", "name": "Example Branch"}]}),
                "financials": evidence("financials", "available", "official_annual_accounts", "https://data.brreg.no/", value={"records": [{"scope": "exact_legal_entity", "period": {"from": "2025-01-01", "to": "2025-12-31"}, "currency": "NOK", "revenue": 1200, "assets": 500}]}),
            },
        }
        result = attach_research_result(profile)["research_result"]
        self.assertTrue(any(item["field"] == "registered_role" for item in result["claims"]))
        self.assertTrue(any(item["field"] == "financial_revenue" for item in result["claims"]))
        self.assertTrue(all(item["evidence_fields"] for item in result["claims"]))
        self.assertTrue(profile["synthesis"]["sentences"])
        self.assertTrue(all("claim_ids" in sentence for sentence in profile["synthesis"]["sentences"]))

    def test_group_normalizer_refuses_unidentified_relations(self):
        value = normalize_group({"relasjoner": [{"organisasjonsnummer": "993578843", "navn": "Parent AS", "relasjon": "parent"}, {"navn": "Unidentified"}]}, ORG)
        self.assertEqual(value["relations"], [{"organisation_number": "993578843", "name": "Parent AS", "declared_relationship": "parent"}])

    def test_group_normalizer_handles_documented_nested_children(self):
        value = normalize_group({"organisasjonsnummer": ORG, "children": [{
            "organisasjonsnummer": "993578843", "navn": "Child AS",
            "knytningsform": {"kode": "KDAT", "beskrivelse": "Konsern datter"},
            "parentOrganisasjonsnummer": ORG, "parentNavn": "Example AS",
            "dato": "2026-05-05", "grunnlag": "100%", "children": [],
        }]}, ORG)
        self.assertEqual(value["relations"][0]["organisation_number"], "993578843")
        self.assertEqual(value["relations"][0]["parent_organisation_number"], ORG)
        self.assertEqual(value["relations"][0]["declared_relationship"]["kode"], "KDAT")

    def test_official_adapter_boundary_fails_closed_without_schema(self):
        result = {"schema": "signalpost.internal.research-result/v1", "claims": []}
        self.assertEqual(adapt_research_result(result), result)
        with self.assertRaises(OfficialSchemaUnavailableError):
            adapt_research_result(result, target="builderr")

    def test_actual_runner_connects_official_records_and_viewer(self):
        batch, bulk = self.root / "batch.txt", self.root / "bulk.csv"
        batch.write_text(ORG + "\n", encoding="utf-8")
        with bulk.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode", "naeringskode1.kode", "naeringskode1.beskrivelse"])
            writer.writeheader(); writer.writerow({"organisasjonsnummer": ORG, "navn": "Example AS", "organisasjonsform.kode": "AS", "naeringskode1.kode": "62.010", "naeringskode1.beskrivelse": "Programming"})
        output, profiles, report, viewer = (self.root / "out.jsonl", self.root / "profiles.jsonl", self.root / "report.json", self.root / "viewer.html")
        harness = r'''
import importlib.util, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("connected_runner",sys.argv[1]); runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
from norway_company_agent.official import normalize_financials
def fake(org, modules, fetcher=None):
  financial_statement={"id":7,"regnskapstype":"SELSKAP","virksomhet":{"organisasjonsnummer":org},"regnskapsperiode":{"fraDato":"2025-01-01","tilDato":"2025-12-31"},"valuta":"NOK","resultatregnskapResultat":{"driftsresultat":{"driftsinntekter":{"sumDriftsinntekter":1200}}}}
  values={
   "roles":{"roles":[{"name":"Test Person","role":"Director","group":"Board"}]},
   "locations":{"locations":[{"organisation_number":"910608193","parent_organisation_number":org,"name":"Example Branch"}]},
   "financials":normalize_financials([financial_statement],org),
   "group":{"relations":[{"organisation_number":"993578843","name":"Parent AS","relation":"parent"}]},
   "registry_live":{"business_address":{"city":"Oslo"},"website":"https://example.no/"},
  }
  return ({m:runner.evidence(m,"available","official_source","https://data.brreg.no/",value=values[m],source_row_key=org) for m in modules}, [])
runner.fetch_official_modules=fake
sys.argv=[sys.argv[1],"--organisations",sys.argv[2],"--bulk",sys.argv[3],"--output",sys.argv[4],"--profiles-output",sys.argv[5],"--report",sys.argv[6],"--run-id","connected","--expected-count","1","--modules","registry,accounting_obligation,registry_live,roles,locations,financials,group","--workers","1","--network-policy","authorized_public_brreg","--confirm-authorized-live-sources"]
runner.main()
'''
        env = os.environ.copy(); env["TEMP"] = str(self.root); env["TMP"] = str(self.root)
        process = subprocess.run([sys.executable, "-S", "-c", textwrap.dedent(harness), str(ROOT / "scripts" / "run_competition_batch.py"), str(batch), str(bulk), str(output), str(profiles), str(report)], cwd=ROOT, text=True, capture_output=True, env=env, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        profile = json.loads(profiles.read_text(encoding="utf-8").splitlines()[0])
        fields = {item["field"] for item in profile["research_result"]["claims"]}
        self.assertTrue({"registered_role", "registered_subunit", "financial_revenue", "corporate_relationship"}.issubset(fields))
        viewer_process = subprocess.run([sys.executable, "-S", "scripts/build_profile_viewer.py", "--profiles", str(profiles), "--output", str(viewer)], cwd=ROOT, text=True, capture_output=True, env=env)
        self.assertEqual(viewer_process.returncode, 0, viewer_process.stderr)
        page = viewer.read_text(encoding="utf-8")
        self.assertIn("Example AS", page)
        self.assertIn("Evidence-grounded overview", page)


if __name__ == "__main__":
    unittest.main()
