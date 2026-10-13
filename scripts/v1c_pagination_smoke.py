#!/usr/bin/env python3
"""Offline 100-company V1c pagination integration; never represents a live run."""
import csv
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v1_official.runner import run
from v0_kernel.storage import read_jsonl


def orgnr(first8):
    weights = (3, 2, 7, 6, 5, 4, 3, 2)
    check = (11 - sum(int(x)*w for x,w in zip(first8, weights)) % 11) % 11
    return first8 + str(check) if check != 10 else None


def main():
    ids = []
    for n in range(91000000, 91000500):
        code = orgnr(str(n))
        if code:
            ids.append(code)
        if len(ids) == 180:
            break
    parents = ids[:100]
    locations = ids[100:180]
    with tempfile.TemporaryDirectory(prefix="v1c-synthetic-") as tmp:
        d = Path(tmp)
        inp, reg, fixture, out, rep = [d/x for x in
                                      ("ids.jsonl","registry.csv","responses.json","result.jsonl","report.json")]
        inp.write_text(''.join(json.dumps({"organisation_number": n})+'\n' for n in parents), encoding="utf-8")
        with reg.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['organisasjonsnummer','navn','organisasjonsform.kode'])
            writer.writeheader()
            for i,n in enumerate(parents):
                writer.writerow({'organisasjonsnummer':n,'navn':f'SYNTHETIC COMPANY {i} AS',
                                 'organisasjonsform.kode':'AS'})
        source={}
        for i,n in enumerate(parents):
            loc = {'body': {'_embedded': {'underenheter': []},
                            'page': {'number':0, 'totalPages':1, 'totalElements':0}}}
            if i < 40:
                u, v = locations[2*i:2*i+2]
                loc = {'pages': [
                    {'_embedded': {'underenheter': [
                        {'organisasjonsnummer':u, 'overordnetEnhet':n, 'navn':f'WORKPLACE {u}'}]},
                     'page': {'number':0,'totalPages':2,'totalElements':2}},
                    {'_embedded': {'underenheter': [
                        {'organisasjonsnummer':v, 'overordnetEnhet':n, 'navn':f'WORKPLACE {v}'}]},
                     'page': {'number':1,'totalPages':2,'totalElements':2}}]}
            source[n]={'roles': {'body': {'rollegrupper':[]}},
                       'locations': loc, 'financials': {'body': []},
                       'registry_live': {'body': {'organisasjonsnummer':n,'navn':f'SYNTHETIC COMPANY {i} AS'}}}
        fixture.write_text(json.dumps(source),encoding='utf-8')
        result=run(organisations=inp,bulk=reg,output=out,report=rep,
                   run_id='V1C_SYNTHETIC_PAGINATION_100',expected_count=100,
                   expected_bulk_sha256=None,fixture_mode=True,
                   fixture_responses=fixture,max_requests=500,max_seconds=30,
                   checkpoint_every=10)
        rows=read_jsonl(out)
        nclaims=sum(c['field']=='registered_subunit' for r in rows for c in r['claims'])
        page_evidence=sum(e.get('source_page_locator') is not None for r in rows for e in r['evidence'])
        complete=all(r.get('official_modules',{}).get('locations',{}).get('source_complete') for r in rows)
        checks={
            'all_100_envelopes':len(rows)==100,
            'input_order_preserved':[r.get('organisation_number') for r in rows]==parents,
            'v1_evidence_validator_pass':result['validation']['passed'] is True,
            'complete_run':result['run_status']=='completed',
            'locations_fully_paged':complete,
            'expected_80_subunits':nclaims==80,
            'per_page_evidence':page_evidence==80,
            'no_outbound_requests':result['source_requests']==0,
            'explicit_synthetic_mode':result['source_mode']=='SYNTHETIC_FIXTURE',
            'no_official_claim':result['official_release_ready'] is False,
        }
        summary={'scenario':'V1C_SYNTHETIC_PAGINATED_100','all_passed':all(checks.values()),
                 'checks':checks,'output_count':len(rows),'subunit_claims':nclaims,
                 'source_requests':result['source_requests'],
                 'run_status':result['run_status']}
        print(json.dumps(summary,indent=2))
        return 0 if summary['all_passed'] else 1

if __name__=='__main__':
    raise SystemExit(main())
