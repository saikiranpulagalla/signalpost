"""Permanent adversarial gates added after both V2a deep failure hunts."""
import ast
import hashlib
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
from v1_official.sources import fixture_fetch, endpoint
from v2_web.identity import Candidate, candidate_from_registry, verify_saved_page
from v2_web.safe_http import HTTPResult, fetch_one_homepage, FetchedPage, verify_diagnostic_receipt
from v2_web.robots import robots_permission
from norway_company_agent.identity import apply_website_identity_gate, assess_website_identity
from norway_company_agent.batch import terminal_envelope

ORG='993578843'
OTHER='923609016'
NAME='SYNTHETIC ALPHA AS'
NOW='2026-10-09T00:00:00Z'


def candidate():
    body={'organisasjonsnummer':ORG,'navn':NAME,'hjemmeside':'https://alpha.example.com'}
    r=fixture_fetch('registry_live',ORG,{ORG:{'registry_live':{'body':body}}})
    return candidate_from_registry(ORG, NAME, r)


def verdict(html, **kw):
    return verify_saved_page(candidate(),html,NOW,**kw)


class IdentityIntegrity(unittest.TestCase):
    def test_hidden_variants_never_exact(self):
        for wrapper in ('hidden','style="display:none"','aria-hidden="true"',
                        'style="visibility:hidden"'):
            with self.subTest(wrapper=wrapper):
                s=f'<div {wrapper}>{NAME} Org.nr {ORG}</div><h1>OTHER PAGE</h1>'
                self.assertNotEqual(verdict(s).status,'exact_page_identity')

    def test_name_number_unrelated_dom_blocks(self):
        html=f'<p>{NAME} is our customer.</p><p>Our owner DIFFERENT AS org.nr {ORG}</p>'
        self.assertNotEqual(verdict(html).status,'exact_page_identity')

    def test_title_body_not_accidentally_paired(self):
        s=f'<html><head><title>{NAME}</title></head><body><p>DIFFERENT AS Org.nr {ORG}</p></body></html>'
        self.assertNotEqual(verdict(s).status,'exact_page_identity')

    def test_exact_visible_legal_footer_preserved(self):
        self.assertEqual(verdict(f'<footer>{NAME} - Org.nr {ORG}</footer>').status,'exact_page_identity')

    def test_group_brand_scope_requires_review(self):
        s=f'<h1>ALPHA GROUP has 20 subsidiaries</h1><footer>{NAME} Org.nr {ORG}</footer>'
        self.assertEqual(verdict(s).status,'group_review')

    def test_unrelated_partner_number_does_not_invalidate_footer(self):
        s=f'<footer>{NAME} Org.nr {ORG}</footer><article>Partner OTHER AS Org.nr {OTHER}</article>'
        self.assertEqual(verdict(s).status,'exact_page_identity')

    def test_domain_sale_article_is_not_parked_domain(self):
        s=f'<footer>{NAME} Org.nr {ORG}</footer><article>How to avoid buy this domain scams</article>'
        self.assertEqual(verdict(s).status,'exact_page_identity')

    def test_meta_refresh_requires_review(self):
        s=f'<meta http-equiv="refresh" content="0;url=https://evil.example"><footer>{NAME} Org.nr {ORG}</footer>'
        self.assertEqual(verdict(s).status,'review')

    def test_cross_origin_canonical_requires_review(self):
        s=f'<link rel="canonical" href="https://elsewhere.example/"><footer>{NAME} Org.nr {ORG}</footer>'
        self.assertEqual(verdict(s).status,'review')

    def test_soft_access_denial_requires_review(self):
        s=f'<h1>ACCESS DENIED - CAPTCHA</h1><footer>{NAME} Org.nr {ORG}</footer>'
        self.assertEqual(verdict(s).status,'review')

    def test_invalid_future_and_cutoff_times(self):
        for ts in ('garbage','2100-01-01T00:00:00Z','2026-10-08 00:00:00'):
            with self.subTest(ts=ts):
                self.assertEqual(verify_saved_page(candidate(),f'{NAME} Org.nr {ORG}',ts).status,'blocked')
        self.assertEqual(verdict(f'{NAME} Org.nr {ORG}',evidence_cutoff='2026-10-08T00:00:00Z').status,'blocked')
        self.assertEqual(verdict(f'{NAME} Org.nr {ORG}',evidence_cutoff=NOW).status,'exact_page_identity')

    def test_malformed_registry_record_isolated(self):
        body={'organisasjonsnummer':ORG,'navn':None,'hjemmeside':'alpha.example.com'}
        r=fixture_fetch('registry_live', ORG,{ORG:{'registry_live':{'body':body}}})
        self.assertIsNone(candidate_from_registry(ORG,NAME,r))

    def test_forged_candidate_rejected(self):
        c=replace(candidate(), registry_source_url='',registry_source_sha256='bad')
        self.assertEqual(verify_saved_page(c,f'{NAME} Org.nr {ORG}',NOW).status,'blocked')
        c=replace(candidate(),source_mode='LIVE_CANDIDATE')
        self.assertEqual(verify_saved_page(c,f'{NAME} Org.nr {ORG}',NOW).status,'blocked')

    def test_claim_publication_always_disabled(self):
        self.assertFalse(verdict(f'{NAME} Org.nr {ORG}').publishable)

    def test_norwegian_vat_and_spaced_number(self):
        for idtext in (f'NO {ORG} MVA', '993  578  843'):
            self.assertEqual(verdict(f'<footer>{NAME}, Org.nr: {idtext}</footer>').status,'exact_page_identity')

    def test_declared_latin1_page(self):
        c=replace(candidate(),legal_name='CAFÉ AS')
        raw=(f'<meta charset="ISO-8859-1"><footer>CAFÉ AS Org.nr {ORG}</footer>').encode('iso-8859-1')
        self.assertEqual(verify_saved_page(c,raw,NOW).status,'exact_page_identity')


class RobotsIntegrity(unittest.TestCase):
    def allowed(self,txt,path):
        return robots_permission(txt.encode(),user_agent='Signalpost-Research/0.2',url='https://alpha.example.com'+path)[0]

    def test_most_specific_rule_independent_of_order(self):
        for txt in ('User-agent: *\nAllow: /\nDisallow: /secret\n',
                    'User-agent: *\nDisallow: /secret\nAllow: /\n'):
            self.assertFalse(self.allowed(txt,'/secret/report'))

    def test_repeated_matching_agent_groups_merged(self):
        txt='User-agent: Signalpost-Research\nAllow: /\nUser-agent: Signalpost-Research\nDisallow: /secret\n'
        self.assertFalse(self.allowed(txt,'/secret/report'))

    def test_allow_most_specific_and_tie(self):
        txt='User-agent: *\nDisallow: /secret\nAllow: /secret/public\n'
        self.assertTrue(self.allowed(txt,'/secret/public'))
        self.assertFalse(self.allowed(txt,'/secret/private'))
        txt='User-agent: *\nDisallow: /same\nAllow: /same\n'
        self.assertTrue(self.allowed(txt,'/same'))

    def test_pacing_directive_requires_scheduler(self):
        txt='User-agent: *\nCrawl-delay: 30\nAllow: /\n'
        self.assertFalse(self.allowed(txt,'/'))

    def test_robots_deny_prevents_page_get(self):
        calls=[]
        def fake(h,ip,p,**kwargs):
            calls.append(p)
            return HTTPResult(200,b'User-agent: *\nDisallow: /secret\n','text/plain')
        c=replace(candidate(),url='https://alpha.example.com/secret/report')
        with self.assertRaises(PermissionError):
            fetch_one_homepage(c,allowed_hosts={'alpha.example.com'},resolver=lambda _: '8.8.8.8',transport=fake)
        self.assertEqual(calls,['/robots.txt'])

    def test_non_html_mime_and_gzip_fail(self):
        for contenttype, content, encoding in (
            ('application/nottext/html',b'<p>ok</p>','identity'),
            ('text/html',b'\x1f\x8bzzz','identity'),
            ('text/html',b'x','gzip')):
            with self.subTest(mime=contenttype,encoding=encoding):
                def fake(h,ip,p,**kw):
                    if p=='/robots.txt':return HTTPResult(404,b'','text/plain')
                    return HTTPResult(200,content,contenttype,encoding)
                with self.assertRaises(ValueError):
                    fetch_one_homepage(candidate(),allowed_hosts={'alpha.example.com'},resolver=lambda _: '8.8.8.8',transport=fake)

    def test_shared_budget_stops_before_page(self):
        calls=[]
        def fake(h,ip,p,**kw):
            calls.append(p)
            time.sleep(.025)
            return HTTPResult(404,b'','text/plain')
        with self.assertRaises(TimeoutError):
            fetch_one_homepage(candidate(),allowed_hosts={'alpha.example.com'},timeout=.01,resolver=lambda _: '8.8.8.8',transport=fake)
        self.assertEqual(calls,['/robots.txt'])

    def test_receipt_contains_body_digest_and_no_publication(self):
        def fake(h,ip,p,**kwargs):
            if p=='/robots.txt':return HTTPResult(404,b'','text/plain')
            return HTTPResult(200,b'<footer>SAFE</footer>','text/html')
        rcpt=fetch_one_homepage(candidate(),allowed_hosts={'alpha.example.com'},resolver=lambda _: '8.8.8.8',transport=fake,return_receipt=True)
        self.assertIsInstance(rcpt,FetchedPage)
        self.assertEqual(rcpt.body_sha256,hashlib.sha256(rcpt.body).hexdigest())
        self.assertFalse(rcpt.publishable)

    def test_diagnostic_receipt_prevents_swapped_bytes_and_url(self):
        body=f'<footer>{NAME} Org.nr {ORG}</footer>'.encode()
        receipt=FetchedPage('https://alpha.example.com/','https://alpha.example.com/',
                            '8.8.8.8',body,hashlib.sha256(body).hexdigest(),NOW,
                            'text/html','robots_absent_404')
        self.assertEqual(verify_diagnostic_receipt(candidate(),receipt,allowed_hosts={'alpha.example.com'}).status,
                         'exact_page_identity')
        for tampered in (replace(receipt,body=b'other'),
                         replace(receipt,request_url='https://other.example/'),
                         replace(receipt,pinned_ip='127.0.0.1'),
                         replace(receipt,source_mode='LIVE'),
                         replace(receipt,body_sha256='a'*64)):
            with self.subTest(tampered=tampered):
                self.assertEqual(verify_diagnostic_receipt(candidate(),tampered,allowed_hosts={'alpha.example.com'}).status,'blocked')
        self.assertEqual(verify_diagnostic_receipt(candidate(),receipt,allowed_hosts=set()).status,'blocked')

    def test_receipt_cutoff_is_enforced(self):
        body=f'<footer>{NAME} Org.nr {ORG}</footer>'.encode()
        receipt=FetchedPage('https://alpha.example.com/','https://alpha.example.com/',
                            '8.8.8.8',body,hashlib.sha256(body).hexdigest(),NOW,
                            'text/html','robots_absent_404')
        self.assertEqual(verify_diagnostic_receipt(candidate(),receipt,allowed_hosts={'alpha.example.com'},
                                                   evidence_cutoff='2026-10-08T00:00:00Z').status,'blocked')


class LegacyInterlocks(unittest.TestCase):
    def test_old_name_only_profile_never_publishable(self):
        profile={'organisation_number':ORG,'name':NAME}
        website={'status':'available','source_url':'https://wrong.example/','value':{
            'title':f'{NAME} | Official','main_text_excerpt':'wrong brand','social_links':[
                {'platform':'linkedin','url':'http://127.0.0.1/private'}]}}
        output=apply_website_identity_gate(profile,website)
        self.assertFalse(output['assessment']['publishable'])
        self.assertEqual(output['assessment']['status'],'review')
        self.assertEqual(output['website']['status'],'blocked')
        self.assertEqual(output['website']['value']['social_links'],[])

    def test_final_terminal_envelope_quarantines_preexisting_web(self):
        profile={'organisation_number':ORG,'evidence':{'website':{'status':'available','value':{
            'social_links':[{'platform':'linkedin','url':'https://linkedin.com/company/x'}],
            'published_claims':['forged']}}}}
        result=terminal_envelope(profile,run_id='test',modules=['website'],started_at=NOW,completed_at=NOW)
        rec=result['profile']['evidence']['website']
        self.assertEqual(rec['status'],'blocked')
        self.assertEqual(rec['value']['website_claims_published'],0)
        self.assertNotIn('social_links',rec['value'])
        self.assertNotIn('published_claims',rec['value'])

    def test_legacy_web_crawler_entrypoints_disabled_pre_network(self):
        for script in ('run_brave_discovery.py','run_scrapy_websites.py'):
            tree=ast.parse((ROOT/'scripts'/script).read_text())
            main=next(x for x in tree.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and x.name=='main')
            self.assertIsInstance(main.body[0],ast.Raise)


class DownstreamPublicationInterlocks(unittest.TestCase):
    def test_research_answer_never_uses_historical_web_claim(self):
        from norway_company_agent.research import answer_profile
        row={'organisation_number':ORG,'name':NAME,'evidence':{'website':{
            'status':'available','source_url':'https://wrong.example',
            'value':{'description':'false website claim',
                     'social_links':[{'platform':'linkedin','url':'https://linkedin.com/company/false'}],
                     'identity_assessment':{'publishable':True}}}}}
        answer=answer_profile(row,'social')
        self.assertNotIn('false website claim',str(answer))
        self.assertNotIn('linkedin.com/company/false',str(answer))

    def test_company_site_observations_even_if_forged_are_not_publishable(self):
        from norway_company_agent.external_footprint import publishable_observation, validate_observation
        item={'id':'x','organisation_number':ORG,'platform':'company_site',
              'signal_type':'public_post','source_url':'https://alpha.example.com/news',
              'retrieved_at':NOW,'content_sha256':'a'*64,'exact_entity':True,
              'identity_proof':['forged'],'acquisition_mode':'permitted_public_page',
              'rights_status':'approved','evidence_span':'fake content',
              'source_class':'company_site'}
        self.assertEqual(validate_observation(item),[])
        self.assertFalse(publishable_observation(item))

    def test_old_activity_news_extractors_do_not_publish_forged_identity(self):
        import importlib.util
        for script in ('extract_company_site_activity.py','extract_company_site_news.py'):
            with self.subTest(script=script):
                spec=importlib.util.spec_from_file_location('_site_test_'+script, ROOT/'scripts'/script)
                module=importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                row={'organisation_number':ORG,'evidence':{'website':{'status':'available','value':{
                    'identity_assessment':{'publishable':True},'content_sha256':'a'*64,
                    'pages':[{'url':'https://alpha.example.com/news/story','content_sha256':'a'*64}],
                    'social_links':[]}}}}
                self.assertIsNone(module.observation(row))

    def test_manual_site_seed_cli_disabled(self):
        tree=ast.parse((ROOT/'scripts'/'apply_verified_site_seeds.py').read_text())
        main=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='main')
        self.assertIsInstance(main.body[0],ast.Raise)
        self.assertIn('publishable = False',(ROOT/'scripts'/'normalize_social_links.py').read_text())


if __name__ == '__main__': unittest.main()
