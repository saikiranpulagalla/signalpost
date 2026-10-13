"""Schema-derived Regnskapsregisteret v3 normalization tests.

Fixtures in this file mirror fields documented by the public v3 OpenAPI; they
are deliberately synthetic and are not observations of a real company.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.evidence import evidence
from norway_company_agent.official import normalize_financials
from norway_company_agent.research_result import attach_research_result

ORG = "923609016"


def documented_statement(*, account_type: str = "SELSKAP", organisation_number: str = ORG) -> dict:
    return {
        "id": 123,
        "regnskapstype": account_type,
        "virksomhet": {"organisasjonsnummer": organisation_number, "organisasjonsform": "AS"},
        "regnskapsperiode": {"fraDato": "2024-01-01", "tilDato": "2024-12-31"},
        "valuta": "NOK",
        "resultatregnskapResultat": {
            "driftsresultat": {
                "driftsinntekter": {"sumDriftsinntekter": 1000},
                "driftsresultat": 100,
            },
            "ordinaertResultatFoerSkattekostnad": 80,
            "aarsresultat": 60,
        },
        "eiendeler": {"sumEiendeler": 500},
        "egenkapitalGjeld": {
            "egenkapital": {"sumEgenkapital": 250},
            "gjeldOversikt": {"sumGjeld": 250},
        },
    }


class FinancialOpenApiNormalizationTests(unittest.TestCase):
    def test_documented_company_statement_maps_exact_metrics_and_period(self):
        value = normalize_financials([documented_statement()], ORG)
        self.assertEqual(value["rejected_records"], [])
        record = value["records"][0]
        self.assertEqual(record["scope"], "exact_legal_entity")
        self.assertEqual(record["period"], {"fraDato": "2024-01-01", "tilDato": "2024-12-31"})
        self.assertEqual(record["currency"], "NOK")
        self.assertEqual({key: record[key] for key in ("revenue", "operating_result", "profit_before_tax", "annual_result", "assets", "equity", "debt")},
                         {"revenue": 1000, "operating_result": 100, "profit_before_tax": 80, "annual_result": 60, "assets": 500, "equity": 250, "debt": 250})

    def test_group_statement_is_preserved_but_never_published_as_entity_financials(self):
        normalized = normalize_financials([documented_statement(account_type="KONSERN")], ORG)
        self.assertEqual(normalized["records"][0]["scope"], "consolidated_group")
        profile = {"organisation_number": ORG, "evidence": {
            "financials": evidence("financials", "available", "official_annual_accounts", "https://data.brreg.no/", value=normalized),
        }}
        result = attach_research_result(profile)["research_result"]
        self.assertFalse(any(claim["field"].startswith("financial_") for claim in result["claims"]))

    def test_wrong_company_bad_period_and_absent_amount_are_not_published(self):
        wrong = documented_statement(organisation_number="993578843")
        bad_period = documented_statement(); bad_period["regnskapsperiode"] = {"fraDato": "2024-12-31", "tilDato": "2024-01-01"}
        sparse = documented_statement()
        del sparse["resultatregnskapResultat"]["driftsresultat"]["driftsinntekter"]["sumDriftsinntekter"]
        normalized = normalize_financials([wrong, bad_period, sparse], ORG)
        self.assertEqual([item["reason"] for item in normalized["rejected_records"]], ["organisation_number_mismatch", "invalid_reporting_period"])
        self.assertEqual(len(normalized["records"]), 1)
        self.assertIsNone(normalized["records"][0]["revenue"])
        profile = {"organisation_number": ORG, "evidence": {
            "financials": evidence("financials", "available", "official_annual_accounts", "https://data.brreg.no/", value=normalized),
        }}
        fields = {claim["field"] for claim in attach_research_result(profile)["research_result"]["claims"]}
        self.assertNotIn("financial_revenue", fields)
        self.assertIn("financial_assets", fields)


if __name__ == "__main__":
    unittest.main()
