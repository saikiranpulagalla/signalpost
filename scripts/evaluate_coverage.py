#!/usr/bin/env python3
"""Offline source-support and company-family metrics; no networking or score guess."""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from norway_company_agent.batch import profiles_from_bulk,read_organisation_numbers
from norway_company_agent.coverage_benchmark import evaluate,telemetry_metrics
from norway_company_agent.completion import verify_completion
from v0_kernel.storage import atomic_json,read_jsonl

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for flag in ('profiles','envelopes','organisations','output'):p.add_argument('--'+flag,type=Path,required=True)
    p.add_argument('--evidence-class',required=True,choices=('historical_retained','authorized_new','synthetic','virtual_time'))
    p.add_argument('--bulk',type=Path);p.add_argument('--observation-key',type=Path)
    p.add_argument('--verified-source-snapshots',type=Path);p.add_argument('--report',type=Path)
    p.add_argument('--telemetry',type=Path)
    a=p.parse_args()
    if a.output.exists():p.error('Coverage output must be new; never overwrite an existing artifact')
    if a.verified_source_snapshots and a.output.resolve().is_relative_to(a.verified_source_snapshots.resolve()):
        p.error('Captured source directory is read-only')
    protected=[a.profiles,a.envelopes,a.organisations,a.bulk,a.observation_key,a.report,a.telemetry]
    if a.output.resolve() in {x.resolve() for x in protected if x}:p.error('Output must not overwrite an input/key')
    read=read_jsonl
    orgs=read_organisation_numbers(a.organisations)
    baselines={p['organisation_number']:p for p in profiles_from_bulk(a.bulk,orgs)[0]} if a.bulk else None
    key=a.observation_key.read_bytes() if a.observation_key else None
    if key is not None and len(key)!=32:p.error('Invalid private observation key')
    result=evaluate(read(a.profiles),read(a.envelopes),orgs,evidence_class=a.evidence_class,baselines=baselines,key=key,source_dir=a.verified_source_snapshots)
    if a.telemetry:result['telemetry']=telemetry_metrics(read(a.telemetry),orgs)
    if a.report:
        verify_completion(report=a.report,output=a.envelopes,profiles=a.profiles)
        result['completion_bindings']='PASS'
        result['reported_operations']=json.loads(a.report.read_text())['operations']
    result['input_sha256']={label:hashlib.sha256(path.read_bytes()).hexdigest() for label,path in (
        ('profiles',a.profiles),('envelopes',a.envelopes),('batch',a.organisations))}
    atomic_json(a.output,result)
    print(json.dumps({k:result[k] for k in ('evidence_class','companies','distinct_declared_facts','metadata_complete_claims','independently_replayable_claims','export_missing_facts','invalid_citation_references')}))
if __name__=='__main__':main()
