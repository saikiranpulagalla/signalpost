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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    dest = args.output_dir
    dest.mkdir(parents=True, exist_ok=False)
    ids = []
    prefix = 10000000
    while len(ids) < 100:
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
    command = [sys.executable, '-B', '-S', str(ROOT/'scripts/run_competition_batch.py'),
        '--organisations', str(dest/'input.jsonl'), '--bulk', str(dest/'frozen.csv'),
        '--modules', 'registry,accounting_obligation', '--network-policy', 'offline',
        '--max-source-requests', '0', '--max-runtime-seconds', '2400',
        '--finalization-reserve-seconds', '120', '--optional-research-policy', 'best_effort',
        '--output-target', 'starter_compatible', '--output', str(dest/'envelopes.jsonl'),
        '--profiles-output', str(dest/'profiles.jsonl'), '--report', str(dest/'report.json'),
        '--viewer-output', str(dest/'viewer.html'), '--run-id', 'synthetic-demo-100']
    subprocess.run(command, check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
    rows, profiles = read_jsonl(dest/'envelopes.jsonl'), read_jsonl(dest/'profiles.jsonl')
    assert len(profiles) == len({p['organisation_number'] for p in profiles}) == 100
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
    assert report['operations']['requests'] == 0
    print(json.dumps({'status':'PASS','synthetic':True,'companies':100,'requests':0,
        'claims':sum(len(r['claims']) for r in rows),'completion':'verified','viewer_profiles_equal':True}))


if __name__ == '__main__':
    main()
