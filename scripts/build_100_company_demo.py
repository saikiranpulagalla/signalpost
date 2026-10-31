#!/usr/bin/env python3
"""Reproduce the SYNTHETIC, zero-network 100-company public viewer.

Creates invented fixture identities, not a company selection or live benchmark.
Refuses an existing destination. Generated authentication keys stay private.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from norway_company_agent.completion import verify_completion
from norway_company_agent.evaluator_adapter import validate_starter_envelopes
from v0_kernel.storage import read_jsonl


def fixture_run(argv):
    """Local synthetic transport only; no public endpoint is contacted."""
    import importlib.util
    import socket
    import urllib.request
    def deny(*a, **k):
        raise AssertionError('Synthetic demo forbids external networking')
    socket.getaddrinfo = socket.create_connection = urllib.request.urlopen = deny
    spec = importlib.util.spec_from_file_location('demo_runner', ROOT/'scripts/run_competition_batch.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    ids = [r['organisation_number'] for r in read_jsonl(Path(argv[argv.index('--organisations')+1]))]
    from norway_company_agent.http import FetchResult
    from norway_company_agent.telemetry import source_organisation_number, utc_now
    def fetch(url, **options):
        org = source_organisation_number(url)
        index = ids.index(org)
        status = 200 if index < 2 else (403 if '/roller' in url and index % 2 else 404)
        if '/roller' in url:
            body = {'organisasjonsnummer':org, 'rollegrupper':[{'type':{'kode':'STYR','beskrivelse':'Board'},
                'roller':[{'type':{'kode':'LEDE','beskrivelse':'Chair'},'person':{'navn':{
                    'fornavn':'SYNTHETIC','etternavn': 'ALPHA' if index == 0 else 'BETA'}}}]}]}
        else:
            body = [{'id':index+1, 'virksomhet':{'organisasjonsnummer':org}, 'regnskapstype':'SELSKAP',
                'regnskapsperiode':{'fraDato':'2025-01-01','tilDato':'2025-12-31'}, 'valuta':'NOK',
                'resultatregnskapResultat':{'driftsresultat':{'driftsinntekter':{'sumDriftsinntekter':
                    1200000 if index == 0 else 2500000}},'aarsresultat':125000 if index == 0 else -10000}}]
        if status != 200:
            body = None
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        return FetchResult(url,status,0,len(raw),body=body,content_sha256=hashlib.sha256(raw).hexdigest(),
            retrieved_at=utc_now(),raw_bytes=raw,error=None if status==200 else 'SYNTHETIC fixture response')
    runner.fetch_json = fetch
    sys.argv = [str(ROOT/'scripts/run_competition_batch.py'),*argv]
    runner.main()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--count', type=int, choices=(100,1000), default=100)
    args = parser.parse_args()
    dest = args.output_dir
    dest.mkdir(parents=True, exist_ok=False)
    ids = []
    prefix = 10000000
    while len(ids) < args.count:
        check = 11 - sum(int(a)*b for a,b in zip(str(prefix), (3,2,7,6,5,4,3,2))) % 11
        if check == 11:
            check = 0
        if check != 10:
            ids.append(str(prefix) + str(check))
        prefix += 1
    (dest/'input.jsonl').write_text(''.join(json.dumps({'organisation_number':o})+'\n' for o in ids), encoding='utf-8')
    with (dest/'frozen.csv').open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['organisasjonsnummer','navn','organisasjonsform.kode'])
        for i,o in enumerate(ids, 1):
            w.writerow([o, f'SYNTHETIC DEMO {i:03d} AS', 'AS'])
    command = [sys.executable, '-B', '-S', '-c',
        'import sys; from scripts.build_100_company_demo import fixture_run; fixture_run(sys.argv[1:])',
        '--organisations', str(dest/'input.jsonl'), '--bulk', str(dest/'frozen.csv'),
        '--modules', 'registry,accounting_obligation,financials,roles',
        '--network-policy', 'authorized_public_brreg', '--confirm-authorized-live-sources',
        '--source-allocation-policy', 'core_breadth', '--source-latency-model', 'config/brreg-admission-observed-20261013.json',
        '--source-attempts','1','--source-timeout-seconds','10',
        '--max-source-requests', str(20*args.count), '--max-runtime-seconds', '2400',
        '--finalization-reserve-seconds', '120', '--optional-research-policy', 'best_effort',
        '--output-target', 'starter_compatible', '--output', str(dest/'envelopes.jsonl'),
        '--profiles-output', str(dest/'profiles.jsonl'), '--report', str(dest/'report.json'),
        '--viewer-output', str(dest/'viewer.html'), '--run-id', 'synthetic-demo-100']
    subprocess.run(command, check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
    rows, profiles = read_jsonl(dest/'envelopes.jsonl'), read_jsonl(dest/'profiles.jsonl')
    assert len(profiles) == len({p['organisation_number'] for p in profiles}) == args.count
    assert validate_starter_envelopes(rows, ids)['passed']
    verify_completion(report=dest/'report.json', output=dest/'envelopes.jsonl', profiles=dest/'profiles.jsonl')
    html = (dest/'viewer.html').read_text(encoding='utf-8')
    embedded = json.loads(re.search(r'id="profiles">(.*?)</script>',html,re.S).group(1))
    assert embedded == profiles
    # Independent physical-LF count/hash checks, separate from application reader.
    for name in ('profiles.jsonl','envelopes.jsonl'):
        records = [json.loads(x) for x in (dest/name).read_bytes().split(b'\n') if x.strip()]
        assert [r['organisation_number'] for r in records] == ids
    manifest = json.loads((dest/'report.json.completion.json').read_text())
    for key,name in [('profiles_sha256','profiles.jsonl'),('envelope_sha256','envelopes.jsonl'),('report_sha256','report.json')]:
        assert manifest[key] == hashlib.sha256((dest/name).read_bytes()).hexdigest()
    report = json.loads((dest/'report.json').read_text())
    assert report['operations']['requests'] <= 20*args.count
    # At official size the time model intentionally admits only a subset;
    # the two contrasting sample records are guaranteed only in the 100 demo.
    for index, amount in (((0,125000),(1,-10000)) if args.count == 100 else ()):
        cs = profiles[index]['research_result']['claims']
        assert any(c['field']=='financial_annual_result' and c['value']['amount']==amount for c in cs)
        assert any(c['field']=='registered_role' for c in cs)
    print(json.dumps({'status':'PASS','synthetic':True,'companies':args.count,'external_requests':0,
        'fixture_starts':report['operations']['requests'],
        'claims':sum(len(r['claims']) for r in rows),'completion':'verified','viewer_profiles_equal':True}))


if __name__ == '__main__':
    main()
