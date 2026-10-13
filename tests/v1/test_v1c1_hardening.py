"""V1c.1 regression bank for the seven independently reproduced V1c failures.

Fixture transport is synthetic; none of these tests claim live BRREG acceptance.
"""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_official_integration import ORG_A, ORG_B, ROOT, example_fixture
from test_v1c_pagination import item, page, wire
from v0_kernel.storage import read_jsonl
from v1_official.runner import run
from v1_official.sources import (
    SourceResponse, MAX_BYTES, assemble_location_pages, canonical_bytes,
    fixture_fetch, live_fetch, location_page_url, origin_observation_for_fact,
    sha, source_is_complete, verified_payload,
)


class V1c1Adversarial(unittest.TestCase):
    def execute(self, fetcher, *, cutoff=None, checkpoint_every=1):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / 'output.jsonl'
            report = run(organisations=ROOT / 'tests/v0/demo/organisations.jsonl',
                         bulk=ROOT / 'tests/v0/demo/frozen-registry.csv',
                         output=out, report=Path(t) / 'report.json',
                         run_id='v1c1-adversarial', expected_count=2, expected_bulk_sha256=None,
                         fixture_mode=True, fetcher=fetcher,
                         evidence_cutoff=cutoff, max_requests=32,
                         max_seconds=30, checkpoint_every=checkpoint_every)
            return report, read_jsonl(out)

    @staticmethod
    def claims(row):
        return [c for c in row['claims'] if c.get('field') == 'registered_subunit']

    def test_invalid_first_page_cannot_claim_completeness(self):
        invalid = page(99, 1, [item(ORG_A, ORG_B)], count=1)
        self.assertFalse(source_is_complete('locations', invalid))
        with patch('v1_official.sources._single_fetch_url', return_value=wire(ORG_A, 0, invalid)):
            result = live_fetch('locations', ORG_A, max_requests=2, policy="authorized_public_brreg")
        self.assertEqual(result.status, 0)
        self.assertIn('invalid first-page', result.error)

    def test_invalid_total_types_do_not_pass_completion(self):
        for bad in (True, '1', None, -1, 0):
            sample = page(0, 1, [item(ORG_A, ORG_B)], count=1)
            sample['page']['totalPages'] = bad
            self.assertFalse(source_is_complete('locations', sample))

    def test_duplicate_legal_subunit_id_blocks_completeness(self):
        a = wire(ORG_A, 0, page(0, 2, [item(ORG_A, ORG_B)], count=2))
        b = wire(ORG_A, 1, page(1, 2, [item(ORG_A, ORG_B)], count=2))
        with self.assertRaisesRegex(ValueError, 'Duplicated subunit'):
            assemble_location_pages(ORG_A, [a, b])
        with patch('v1_official.sources._single_fetch_url', side_effect=[a, b]):
            result = live_fetch('locations', ORG_A, max_requests=2, policy="authorized_public_brreg")
        self.assertFalse(source_is_complete('locations', result.body))
        self.assertEqual(result.continuation_state, 'inconsistent_pages')
        self.assertEqual(result.requests, 2)
        self.assertEqual(len(result.pages), 1)

    def test_per_page_retrieved_at_is_not_last_page_timestamp(self):
        a = replace(wire(ORG_A, 0, page(0, 2, [item(ORG_A, ORG_B)], count=2)),
                    retrieved_at='2026-10-08T10:00:00Z')
        b = replace(wire(ORG_A, 1, page(1, 2, [item(ORG_A, '978617360')], count=2)),
                    retrieved_at='2026-10-08T10:04:00Z')
        response = assemble_location_pages(ORG_A, [a, b])
        self.assertTrue(verified_payload(response))
        self.assertEqual(origin_observation_for_fact(response, '$._embedded.underenheter[0]')[3], a.retrieved_at)
        self.assertEqual(origin_observation_for_fact(response, '$._embedded.underenheter[1]')[3], b.retrieved_at)
        fixtures = example_fixture()
        report, rows = self.execute(lambda mod, org: response if (mod, org) == ('locations', ORG_A)
                                    else fixture_fetch(mod, org, fixtures))
        self.assertTrue(report['validation']['passed'], report)
        locations = {c['value']['orgnr']: c for c in self.claims(rows[0])}
        evidence = {e['id']: e for e in rows[0]['evidence']}
        self.assertEqual(evidence[locations[ORG_B]['evidence_ids'][0]]['retrieved_at'], a.retrieved_at)
        self.assertEqual(evidence[locations['978617360']['evidence_ids'][0]]['retrieved_at'], b.retrieved_at)

    def test_cutoff_later_page_preserves_earlier_source(self):
        a = replace(wire(ORG_A, 0, page(0, 2, [item(ORG_A, ORG_B)], count=2)),
                    retrieved_at='2026-10-08T10:00:00Z')
        b = replace(wire(ORG_A, 1, page(1, 2, [item(ORG_A, '978617360')], count=2)),
                    retrieved_at='2026-10-08T10:10:00Z')
        response = assemble_location_pages(ORG_A, [a, b])
        fixture = example_fixture()
        result, rows = self.execute(lambda mod, org: response if (mod, org) == ('locations', ORG_A)
                                    else fixture_fetch(mod, org, fixture),
                                    cutoff='2026-10-08T10:05:00Z')
        self.assertEqual(result['run_status'], 'completed', result)
        self.assertTrue(result['validation']['passed'], result)
        self.assertEqual([c['value']['orgnr'] for c in self.claims(rows[0])], [ORG_B])
        meta = rows[0]['official_modules']['locations']
        self.assertFalse(meta['source_complete'])
        self.assertEqual(meta['continuation_state'], 'cutoff_limited')
        self.assertTrue(any(e.get('module') == 'locations' and e['retrieved_at'] == a.retrieved_at for e in rows[0]['evidence']))

    def test_cutoff_first_page_blocks_all_pages(self):
        a = replace(wire(ORG_A, 0, page(0, 2, [item(ORG_A, ORG_B)], count=2)),
                    retrieved_at='2026-10-08T10:10:00Z')
        b = replace(wire(ORG_A, 1, page(1, 2, [item(ORG_A, '978617360')], count=2)),
                    retrieved_at='2026-10-08T10:00:00Z')
        response = assemble_location_pages(ORG_A, [a, b])
        fixture = example_fixture()
        report, rows = self.execute(lambda mod, org: response if (mod, org) == ('locations', ORG_A)
                                    else fixture_fetch(mod, org, fixture),
                                    cutoff='2026-10-08T10:05:00Z')
        self.assertEqual(report['run_status'], 'completed')
        self.assertFalse(self.claims(rows[0]))
        self.assertEqual(rows[0]['availability']['locations'], 'failed')

    def test_cutoff_equal_timestamp_is_permitted(self):
        a = replace(wire(ORG_A, 0, page(0, 1, [item(ORG_A, ORG_B)], count=1)),
                    retrieved_at='2026-10-08T10:05:00Z')
        fixture = example_fixture()
        report, rows = self.execute(lambda mod, org: a if (mod, org) == ('locations', ORG_A)
                                    else fixture_fetch(mod, org, fixture),
                                    cutoff='2026-10-08T10:05:00Z')
        self.assertEqual(report['run_status'], 'completed')
        self.assertEqual(len(self.claims(rows[0])), 1)

    def test_continuation_429_explicitly_reported_without_losing_fact(self):
        a = wire(ORG_A, 0, page(0, 2, [item(ORG_A, ORG_B)], count=2))
        b = SourceResponse('locations', location_page_url(ORG_A, 1), 429,
                           '2026-10-08T10:00:01Z', error='HTTP 429', requests=1)
        with patch('v1_official.sources._single_fetch_url', side_effect=[a, b]):
            response = live_fetch('locations', ORG_A, max_requests=2, policy="authorized_public_brreg")
        self.assertEqual(response.requests, 2)
        self.assertEqual(response.continuation_state, 'blocked')
        self.assertEqual(response.continuation_http_status, 429)
        self.assertFalse(source_is_complete('locations', response.body))
        fixture = example_fixture()
        report, rows = self.execute(lambda mod, org: response if (mod, org) == ('locations', ORG_A)
                                    else fixture_fetch(mod, org, fixture))
        self.assertTrue(report['validation']['passed'])
        self.assertEqual(len(self.claims(rows[0])), 1)
        self.assertEqual(rows[0]['official_modules']['locations']['continuation_http_status'], 429)

    def test_aggregate_larger_than_two_mib_remains_bounded_and_valid(self):
        padding = 'x' * 1050000
        a = wire(ORG_A, 0, page(0, 2, [{**item(ORG_A, ORG_B), 'other_data': padding}], count=2))
        b = wire(ORG_A, 1, page(1, 2, [{**item(ORG_A, '978617360'), 'other_data': padding}], count=2))
        self.assertLess(len(canonical_bytes(a.body)), MAX_BYTES)
        self.assertLess(len(canonical_bytes(b.body)), MAX_BYTES)
        self.assertGreater(len(canonical_bytes(a.body))+len(canonical_bytes(b.body)), MAX_BYTES)
        with patch('v1_official.sources._single_fetch_url', side_effect=[a, b]):
            response = live_fetch('locations', ORG_A, max_requests=2, policy="authorized_public_brreg")
        self.assertEqual(len(response.pages), 2)
        self.assertTrue(source_is_complete('locations', response.body))
        self.assertTrue(verified_payload(response))
        fixture = example_fixture()
        report, rows = self.execute(lambda mod, org: response if (mod, org) == ('locations', ORG_A)
                                    else fixture_fetch(mod, org, fixture))
        self.assertTrue(report['validation']['passed'], report)
        self.assertEqual(len(self.claims(rows[0])), 2)

    def test_checkpoint_every_one_persists_before_next_source_attempt(self):
        fixture = example_fixture()
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); output = d / 'out.jsonl'; seen = []
            def observe(module, org):
                if seen:
                    persisted = read_jsonl(output)
                    self.assertGreaterEqual(sum(c.get('field') == 'registered_role'
                                                for row in persisted for c in row['claims']), 1)
                    self.assertTrue(all(row['run']['terminal_status'] == 'failed' for row in persisted))
                seen.append((module, org))
                return fixture_fetch(module, org, fixture)
            report = run(organisations=ROOT / 'tests/v0/demo/organisations.jsonl',
                         bulk=ROOT / 'tests/v0/demo/frozen-registry.csv',
                         output=output, report=d / 'report.json', run_id='checkpoint-cadence',
                         expected_count=2, expected_bulk_sha256=None, fixture_mode=True, fetcher=observe,
                         max_requests=32, max_seconds=30, checkpoint_every=1)
            self.assertEqual(report['run_status'], 'completed', report)
            self.assertTrue(report['validation']['passed'])

    def test_stable_sort_is_present_on_all_location_requests(self):
        self.assertIn('sort=organisasjonsnummer,ASC', location_page_url(ORG_A, 0))
        self.assertIn('sort=organisasjonsnummer,ASC', location_page_url(ORG_A, 1))

    def test_out_of_order_distinct_subunits_are_partial_not_complete(self):
        a = wire(ORG_A, 0, page(0, 2, [item(ORG_A, '978617360')], count=2))
        b = wire(ORG_A, 1, page(1, 2, [item(ORG_A, ORG_B)], count=2))
        with self.assertRaisesRegex(ValueError, 'Out-of-order'):
            assemble_location_pages(ORG_A, [a, b])
        with patch('v1_official.sources._single_fetch_url', side_effect=[a, b]):
            response = live_fetch('locations', ORG_A, max_requests=2, policy="authorized_public_brreg")
        self.assertFalse(source_is_complete('locations', response.body))
        self.assertEqual(response.continuation_state, 'inconsistent_pages')

    def test_more_than_ten_pages_preserves_safe_partial_prefix(self):
        a = wire(ORG_A, 0, page(0, 11, [item(ORG_A, ORG_B)], count=11))
        with patch('v1_official.sources._single_fetch_url', return_value=a):
            response = live_fetch('locations', ORG_A, max_requests=1, policy="authorized_public_brreg")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.continuation_state, 'budget_limited')
        self.assertFalse(source_is_complete('locations', response.body))
        self.assertTrue(verified_payload(response))

    def test_bounded_aggregate_overflow_retains_previous_pages(self):
        # Each valid network page remains below 2 MiB; >16 MiB total must
        # stop safely without publishing a false complete result.
        huge = 'y' * 1900000
        pages = [wire(ORG_A, i, page(i, 9, [{**item(ORG_A, f'{920000000+i}'), 'unused': huge}], count=9))
                 for i in range(9)]
        with patch('v1_official.sources._single_fetch_url', side_effect=pages):
            response = live_fetch('locations', ORG_A, timeout=30, max_requests=9, policy="authorized_public_brreg")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.continuation_state, 'size_limited')
        self.assertFalse(source_is_complete('locations', response.body))
        self.assertEqual(response.requests, 9)
        self.assertEqual(len(response.pages), 8)
        self.assertTrue(verified_payload(response))

    def test_source_metadata_cannot_claim_complete_when_partial(self):
        a = wire(ORG_A, 0, page(0, 2, [item(ORG_A, ORG_B)], count=2))
        result = assemble_location_pages(ORG_A, [a], continuation_state='budget_limited')
        self.assertFalse(source_is_complete('locations', result.body))
        self.assertTrue(verified_payload(result))
        self.assertFalse(verified_payload(replace(result, continuation_state='blocked')))


if __name__ == '__main__':
    unittest.main()
