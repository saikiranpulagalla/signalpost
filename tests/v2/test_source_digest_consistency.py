"""Fresh source integrity must not depend on optional completion policy."""
import json
import unittest
import test_optional_research_completion as fixture
from unittest.mock import patch


class SourceDigestConsistency(unittest.TestCase):
    setUp = fixture.OptionalResearchCompletion.setUp
    execute = fixture.OptionalResearchCompletion.execute
    checked = fixture.OptionalResearchCompletion.checked

    def test_strict_rejects_digest_inconsistent_with_available_source_bytes(self):
        process, directory = self.execute('strict-corrupt', 'corrupt', 'strict')
        self.assertNotEqual(process.returncode, 0, process.stdout + process.stderr)
        marker = json.loads((directory/'report.json.completion.json').read_bytes())
        self.assertNotEqual(marker['status'], 'completed')
        output = directory/'envelopes.jsonl'
        if output.exists():
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertFalse(any(e['content_sha256'] == '0'*64 for r in rows for e in r['evidence']))

    def test_both_policies_reject_independent_fresh_source_inconsistencies(self):
        mutations = {
            'digest': "result.content_sha256='0'*64",
            'body': "result.body[0]['resultatregnskapResultat']['aarsresultat']=999999",
            'count': 'result.bytes_received+=1',
            'missing_bytes': 'result.raw_bytes=None',
            'incomplete': 'result.body_complete=False',
            'invalid_json': "result.raw_bytes=b'not JSON';result.bytes_received=len(result.raw_bytes);result.content_sha256=hashlib.sha256(result.raw_bytes).hexdigest()",
        }
        for policy in ('strict', 'best_effort'):
            for name, mutation in mutations.items():
                with self.subTest(policy=policy, mutation=name):
                    harness = fixture.HARNESS.replace(
                        "if mode=='corrupt' and trigger:result.content_sha256='0'*64",
                        "if mode=='corrupt' and trigger:" + mutation)
                    with patch.object(fixture, 'HARNESS', harness):
                        process, directory = self.execute(policy+'-'+name, 'corrupt', policy)
                    self.assertNotEqual(process.returncode, 0)
                    self.assertNotEqual(json.loads((directory/'report.json.completion.json').read_bytes())['status'], 'completed')
                    output = directory/'envelopes.jsonl'
                    if output.exists():
                        rows = [json.loads(line) for line in output.read_text().splitlines()]
                        # The rejected source must never acquire a signed or
                        # exported financial observation; other facts may remain.
                        self.assertFalse(any(e.get('content_sha256') == '0'*64 for r in rows for e in r['evidence']))
                    profiles = [json.loads(line) for line in (directory/'profiles.jsonl').read_text().splitlines()]
                    failures = json.loads((directory/'report.json').read_bytes())['worker_failures']
                    self.assertTrue(failures)
                    # One injected source is rejected independently; legitimate
                    # other-company evidence remains reusable and unchanged.
                    self.assertTrue(any(p.get('observation_receipts') for p in profiles))

    def test_both_policies_keep_identical_nominal_claims(self):
        published = []
        for policy in ('strict', 'best_effort'):
            process, directory = self.execute('nominal-'+policy, 'normal', policy)
            rows, _ = self.checked(process, directory)
            published.append([(r['organisation_number'], [(c['field'], c['value']) for c in r['claims']]) for r in rows])
        self.assertEqual(*published)


if __name__ == '__main__':
    unittest.main()
