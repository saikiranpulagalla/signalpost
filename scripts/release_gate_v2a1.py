#!/usr/bin/env python3
"""Consolidated V2a.1 offline safety gate. Not official Builderr qualification."""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args, *, timeout=480):
    # The desktop sandbox's inherited TEMP directory rejects atomic renames even
    # for a minimal closed-file replacement. Keep all gate-only temp artifacts
    # under a known writable project directory; subprocesses and their tests
    # inherit this setting. This does not change production output semantics.
    temp_root = ROOT / ".release-gate-tmp"
    temp_root.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update({"TMP": str(temp_root), "TEMP": str(temp_root)})
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=timeout, env=env)


def main():
    result = {'status':'LOCAL_V2A1_GATE_BLOCKED','official_release_ready':False,
              'official_v2_schema_verified':False,'official_score':None,
              'real_brreg_100':'NOT_RUN','live_website_crawl':'NOT_RUN',
              'website_claims_published':0}
    try:
        base = run([sys.executable,'-S','scripts/release_gate_v2a.py'],timeout=480)
        if base.returncode:
            raise RuntimeError('V2a inherited gate failed: '+(base.stdout+base.stderr)[-1200:])
        old = json.loads(base.stdout)
        if (old.get('status') != 'LOCAL_V2A_GATE_PASS' or
            old.get('total_local_tests') != 224 or
            old.get('v2a_adversarial_tests') != 27 or
            old.get('synthetic_website_fixture') != 'PASS' or
            old.get('budgeted_1000') != 'PASS' or
            old.get('official_release_ready') is not False):
            raise RuntimeError('Contradictory or missing inherited gate proofs')
        new=run([sys.executable,'-S','-m','unittest','discover','-s','tests/v2',
                 '-p','test_v2a1_hardening.py','-q'],timeout=90)
        output=new.stdout+new.stderr
        counts=re.findall(r'Ran (\d+) tests? in ',output)
        if new.returncode or counts != ['33'] or not re.search(r'^OK\s*$', output, re.M) or 'FAILED' in output:
            raise RuntimeError('V2a.1 adversarial suite did not pass: '+output[-1200:])
        repairs=run([sys.executable,'-S','-m','unittest','discover','-s','tests/v2',
                     '-p','test_v2a2_hardening.py','-q'],timeout=90)
        repair_output=repairs.stdout+repairs.stderr
        repair_counts=re.findall(r'Ran (\d+) tests? in ',repair_output)
        if repairs.returncode or repair_counts != ['9'] or not re.search(r'^OK\s*$', repair_output, re.M) or 'FAILED' in repair_output:
            raise RuntimeError('V2a.2 repair regressions did not pass: '+repair_output[-1200:])
        expected={
            'src/norway_company_agent/identity.py':['"publishable": False','website["status"] = "blocked"'],
            'src/norway_company_agent/website.py':['legacy website fetch disabled'],
            'src/norway_company_agent/batch.py':['final-envelope website publication safety interlock'],
            'v2_web/safe_http.py':['robots_permission','return_receipt'],
            'scripts/run_brave_discovery.py':['legacy external crawler disabled'],
            'scripts/run_scrapy_websites.py':['legacy external crawler disabled'],
            'src/norway_company_agent/research.py':['website_publishable = False'],
            'src/norway_company_agent/external_footprint.py':['if item.get("platform") == "company_site"'],
            'scripts/normalize_social_links.py':['publishable = False'],
            'scripts/extract_company_site_activity.py':['return None  # V2a.1'],
            'scripts/extract_company_site_news.py':['return None  # V2a.1'],
            'scripts/apply_verified_site_seeds.py':['untrusted manual site seed promotion disabled'],
        }
        for rel,markers in expected.items():
            source=(ROOT/rel).read_text(encoding='utf-8')
            for marker in markers:
                if marker not in source:
                    raise RuntimeError('Missing required V2a.1 interlock: '+rel+': '+marker)
        result.update(status='LOCAL_V2A2_GATE_PASS', inherited_local_tests=224,
                      v2a1_adversarial_tests=33, v2a2_repair_tests=9,total_local_tests=266,
                      inherited_budgeted_1000='PASS',inherited_synthetic_paged_100='PASS',
                      legacy_crawlers='DISABLED', legacy_web_publication='DISABLED',
                      robots_policy='OFFLINE_TESTED', live_fetch_receipts='DIAGNOSTIC_ONLY')
        code=0
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,subprocess.TimeoutExpired) as exc:
        result['error']=str(exc)[:1500]
        code=1
    print(json.dumps(result,indent=2,sort_keys=True))
    return code

if __name__=='__main__':raise SystemExit(main())
