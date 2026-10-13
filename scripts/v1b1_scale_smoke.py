#!/usr/bin/env python3
"""Synthetic 1000-company, 500-request stress proof. Never a live BRREG smoke."""
from __future__ import annotations
import csv
from dataclasses import replace
import json
import tempfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v1_official.runner import run
from v1_official.sources import SourceResponse, endpoint, utc_now
from v0_kernel.storage import read_jsonl

WEIGHTS=(3,2,7,6,5,4,3,2)
def number(n):
    code=f'{n:08d}'
    chk=(11-sum(int(c)*w for c,w in zip(code,WEIGHTS))%11)%11
    return code+str(chk) if chk!=10 else None

def main():
    with tempfile.TemporaryDirectory() as t:
        work=Path(t); ids=[]; n=52000000
        while len(ids)<1000:
            org=number(n)
            if org: ids.append(org)
            n+=1
        batch=work/'batch.jsonl';bulk=work/'registry.csv'
        batch.write_text(''.join(json.dumps({'organisation_number':x})+'\n' for x in ids))
        with bulk.open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=['organisasjonsnummer','navn','organisasjonsform.kode'])
            writer.writeheader()
            for idx,org in enumerate(ids):
                writer.writerow({'organisasjonsnummer':org,'navn':f'SYNTHETIC COMPANY {idx} AS','organisasjonsform.kode':'AS'})
        def counted_404(module,org):
            return SourceResponse(module,endpoint(module,org),404,utc_now(),error='fixture HTTP 404',requests=1)
        result=run(organisations=batch,bulk=bulk,output=work/'result.jsonl',
                   report=work/'report.json',run_id='synthetic-budget-1000',
                   expected_count=1000,expected_bulk_sha256=None,fixture_mode=True,
                   fetcher=counted_404,max_requests=500,max_seconds=60,checkpoint_every=50)
        rows=read_jsonl(work/'result.jsonl')
        checks={'completed':result['run_status']=='completed',
                'validation':result['validation']['passed'],
                'exact_envelopes':len(rows)==1000,
                'correct_ids':[r['organisation_number'] for r in rows]==ids,
                'requests_bounded':result['source_requests']<=500,
                'partial_honest':result['partial_research_due_to_budget'],
                'not_official_ready':not result['official_release_ready'],
                'zero_wrong_live_claims':sum(c['field'].startswith('official_') or
                                              c['field']=='registered_role' for r in rows for c in r['claims'])==0}
        summary={'scenario':'SYNTHETIC_1000_500_REQUESTS','checks':checks,
                 'all_passed':all(checks.values()),'source_requests':result['source_requests'],
                 'run_status':result['run_status'],'wall_clock_ms':result['wall_clock_ms'],
                 'zero_network_claim':True}
        print(json.dumps(summary,indent=2))
        return 0 if summary['all_passed'] else 1
if __name__=='__main__': raise SystemExit(main())
