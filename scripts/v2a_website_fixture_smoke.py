#!/usr/bin/env python3
"""Offline exact-entity website candidate smoke. Never a live or Builderr score.

Fixture format:
{"org": {"legal_name": "...", "registry_body": {"organisasjonsnummer":...,
 "navn":..., "hjemmeside":...}, "html": "..."}}
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v0_kernel.contracts import is_valid_orgnr
from v0_kernel.storage import atomic_json
from v1_official.sources import fixture_fetch, utc_now
from v2_web.identity import candidate_from_registry, verify_saved_page


def run_fixture(path: Path) -> dict:
    payload=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload,dict) or not payload or len(payload)>1000:
        raise ValueError('Fixture must be a nonempty bounded organisation map')
    rows=[]
    for org,rec in sorted(payload.items()):
        if not is_valid_orgnr(org) or not isinstance(rec,dict):
            raise ValueError('Invalid fixture company')
        name=rec.get('legal_name')
        if not isinstance(name,str) or not name.strip():
            raise ValueError('Missing legal name')
        source={org:{'registry_live':{'status':200,'body':rec.get('registry_body')}}}
        response=fixture_fetch('registry_live',org,source)
        c=candidate_from_registry(org,name,response)
        if c is None:
            rows.append({'organisation_number':org,'status':'no_verified_candidate', 'publishable':False})
            continue
        raw_html=rec.get('html','')
        if not isinstance(raw_html,str):
            raise ValueError('Fixture HTML must be text')
        verdict=verify_saved_page(c,raw_html,utc_now()).to_dict()
        rows.append({'organisation_number':org,'candidate_url':c.url,'candidate_source_mode':c.source_mode,
                     'identity':verdict,'publishable':False})
    return {'status':'V2A_OFFLINE_FIXTURE_COMPLETE','source_mode':'SYNTHETIC_OFFLINE_FIXTURE',
            'official_v2_schema_verified':False,'official_score':None,
            'website_claims_published':0,'company_count':len(rows),'companies':rows}


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.fixture.resolve()==args.output.resolve():
        p.error('Never overwrite the source fixture')
    try:
        result=run_fixture(args.fixture)
        atomic_json(args.output,result)
        print(json.dumps({'status':result['status'],'companies':result['company_count'],
                          'official_release_ready':False}))
        return 0
    except (OSError,ValueError,TypeError,json.JSONDecodeError) as exc:
        print(json.dumps({'status':'V2A_FIXTURE_BLOCKED','error':str(exc)[:240]}))
        return 1

if __name__=='__main__':raise SystemExit(main())
