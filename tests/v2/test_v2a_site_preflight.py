import hashlib
import json
import unittest
from unittest.mock import patch

from v1_official.sources import fixture_fetch
from v2_web.identity import candidate_from_registry, normalize_url, verify_saved_page, Candidate
from v2_web.safe_http import fetch_one_homepage, HTTPResult, resolve_public

ORG = '993578843'
OTHER = '923609016'
NAME = 'SYNTHETIC ALPHA AS'


def candidate():
    response = fixture_fetch('registry_live', ORG, {ORG: {'registry_live': {
        'body': {'organisasjonsnummer': ORG, 'navn': NAME, 'hjemmeside': 'https://alpha.example.com/'}}}})
    return candidate_from_registry(ORG, NAME, response)


class SitePreflight(unittest.TestCase):
    def test_candidate_from_exact_registry(self):
        c = candidate()
        self.assertIsNotNone(c)
        self.assertEqual(c.url, 'https://alpha.example.com/')
        self.assertEqual(c.source_mode, 'SYNTHETIC')

    def test_candidate_missing_website(self):
        response = fixture_fetch('registry_live', ORG, {ORG: {'registry_live': {
            'body': {'organisasjonsnummer': ORG, 'navn': NAME}}}})
        self.assertIsNone(candidate_from_registry(ORG, NAME, response))

    def test_candidate_mismatched_org(self):
        response = fixture_fetch('registry_live', ORG, {ORG: {'registry_live': {
            'body': {'organisasjonsnummer': OTHER, 'navn': NAME, 'hjemmeside': 'alpha.example.com'}}}})
        self.assertIsNone(candidate_from_registry(ORG, NAME, response))

    def test_candidate_name_mismatch(self):
        response = fixture_fetch('registry_live', ORG, {ORG: {'registry_live': {
            'body': {'organisasjonsnummer': ORG, 'navn': 'OTHER AS', 'hjemmeside': 'alpha.example.com'}}}})
        self.assertIsNone(candidate_from_registry(ORG, NAME, response))

    def test_candidate_tampered_payload(self):
        from dataclasses import replace
        response = fixture_fetch('registry_live', ORG, {ORG: {'registry_live': {
            'body': {'organisasjonsnummer': ORG, 'navn': NAME, 'hjemmeside': 'alpha.example.com'}}}})
        altered = replace(response, body={**response.body, 'hjemmeside': 'evil.example.com'})
        self.assertIsNone(candidate_from_registry(ORG, NAME, altered))

    def test_exact_contextual_identity(self):
        html = f'<html><body><footer>{NAME} — Org.nr: {ORG}</footer></body></html>'
        v = verify_saved_page(candidate(), html, '2026-10-08T00:00:00Z')
        self.assertEqual(v.status, 'exact_page_identity')
        self.assertFalse(v.publishable)
        self.assertEqual(v.found_org_numbers, (ORG,))
        self.assertEqual(v.content_sha256, hashlib.sha256(html.encode()).hexdigest())

    def test_group_with_other_org_is_review(self):
        v = verify_saved_page(candidate(), f'<p>{NAME} org.nr {ORG}</p><p>Subsidiary org.nr {OTHER}</p>', '2026-10-08T00:00:00Z')
        self.assertEqual(v.status, 'group_review')

    def test_other_company_rejected(self):
        v = verify_saved_page(candidate(), f'<p>OTHER AS org.nr {OTHER}</p>', '2026-10-08T00:00:00Z')
        self.assertEqual(v.status, 'rejected')

    def test_name_only_never_exact(self):
        v = verify_saved_page(candidate(), f'<h1>{NAME}</h1>', '2026-10-08T00:00:00Z')
        self.assertEqual(v.status, 'candidate_only')

    def test_org_far_from_name_ambiguous(self):
        v = verify_saved_page(candidate(), NAME + (' x ' * 400) + f'org.nr {ORG}', '2026-10-08T00:00:00Z')
        self.assertEqual(v.status, 'ambiguous')

    def test_js_injected_id_not_identity(self):
        v = verify_saved_page(candidate(), f'<script>org.nr {ORG}</script><h1>{NAME}</h1>', '2026-10-08T00:00:00Z')
        self.assertEqual(v.status, 'candidate_only')

    def test_parked(self):
        v = verify_saved_page(candidate(), f'<h1>{NAME} org.nr {ORG}</h1>Buy this domain', '2026-10-08T00:00:00Z')
        self.assertEqual(v.status, 'rejected')

    def test_mismatched_final_url(self):
        v = verify_saved_page(candidate(), f'{NAME} org.nr {ORG}', '2026-10-08T00:00:00Z', 'https://other.example.com/')
        self.assertEqual(v.status, 'blocked')

    def test_large_html(self):
        v = verify_saved_page(candidate(), b'x' * (512 * 1024 + 1), '2026-10-08T00:00:00Z')
        self.assertEqual(v.status, 'blocked')

    def test_url_restrictions(self):
        for url in ('http://alpha.example.com', 'https://127.0.0.1', 'https://[::1]',
                    'https://localhost', 'https://internal.local', 'https://user:pass@alpha.example.com',
                    'https://alpha.example.com:9443', 'https://alpha.example.com\\@evil.com',
                    'https://alpha.example.com?q=bad%0a', 'https://alpha.example.com?x=1', 'file:///etc/passwd', 'https://alpha.example.com#x',
                    'https://tést.example.com'):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    normalize_url(url)

    def test_missing_source_rights_blocks_without_transport(self):
        called=[]
        def fake(*args, **kwargs):
            called.append(1)
            return HTTPResult(200, b'', 'text/html')
        with self.assertRaises(PermissionError):
            fetch_one_homepage(candidate(), allowed_hosts=set(), resolver=lambda h: '8.8.8.8', transport=fake)
        self.assertFalse(called)

    def test_safe_pinned_host_and_robots(self):
        calls=[]
        def fake(host, ip, path, **kwargs):
            calls.append((host, ip, path))
            if path == '/robots.txt':
                return HTTPResult(200, b'User-agent: *\nAllow: /\n', 'text/plain')
            return HTTPResult(200, f'<p>{NAME} Org.nr {ORG}</p>'.encode(), 'text/html; charset=utf-8')
        html=fetch_one_homepage(candidate(),allowed_hosts={'alpha.example.com'},resolver=lambda h:'8.8.8.8',transport=fake)
        self.assertIn(ORG.encode(),html)
        self.assertEqual(calls,[('alpha.example.com','8.8.8.8','/robots.txt'),('alpha.example.com','8.8.8.8','/')])

    def test_robots_disallow_blocks_homepage(self):
        calls=[]
        def fake(host, ip, path, **kwargs):
            calls.append(path)
            return HTTPResult(200, b'User-agent: *\nDisallow: /\n', 'text/plain')
        with self.assertRaises(PermissionError):
            fetch_one_homepage(candidate(),allowed_hosts={'alpha.example.com'},resolver=lambda h:'8.8.8.8',transport=fake)
        self.assertEqual(calls,['/robots.txt'])

    def test_robots_failure_denies(self):
        def fake(*args, **kwargs):
            return HTTPResult(503, b'', 'text/plain')
        with self.assertRaises(PermissionError):
            fetch_one_homepage(candidate(),allowed_hosts={'alpha.example.com'},resolver=lambda h:'8.8.8.8',transport=fake)

    def test_dns_private_blocked(self):
        calls=[]
        def fake(*args, **kwargs):
            calls.append(1)
        with self.assertRaises(ValueError):
            fetch_one_homepage(candidate(),allowed_hosts={'alpha.example.com'},resolver=lambda h:'127.0.0.1',transport=fake)
        self.assertFalse(calls)

    def test_redirect_blocked(self):
        def fake(host, ip, path, **kwargs):
            if path == '/robots.txt': return HTTPResult(404,b'','text/plain')
            return HTTPResult(302,b'','text/html')
        with self.assertRaises(ValueError):
            fetch_one_homepage(candidate(),allowed_hosts={'alpha.example.com'},resolver=lambda h:'8.8.8.8',transport=fake)

    def test_non_html_blocked(self):
        def fake(host, ip, path, **kwargs):
            if path == '/robots.txt': return HTTPResult(404,b'','text/plain')
            return HTTPResult(200,b'%PDF','application/pdf')
        with self.assertRaises(ValueError):
            fetch_one_homepage(candidate(),allowed_hosts={'alpha.example.com'},resolver=lambda h:'8.8.8.8',transport=fake)

    def test_valid_unicode_legal_name_nfc(self):
        from dataclasses import replace
        c=replace(candidate(),legal_name='CAFÉ AS')
        v=verify_saved_page(c,f'Cafe\u0301 AS Org.nr {ORG}','2026-10-08T00:00:00Z')
        self.assertEqual(v.status,'exact_page_identity')

if __name__=='__main__':unittest.main()
