#!/usr/bin/env python3
"""Pinned public-universe sampling only; selected candidates are not live verified."""
import argparse,gzip,hashlib,heapq,json,sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from norway_company_agent.batch import canonical_organisation_number,read_organisation_numbers
from v0_kernel.storage import atomic_json,atomic_jsonl

def select(path:Path,*,digest:str,count:int,seed:str,excluded:set[str]):
    actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual!=digest:raise ValueError('Universe digest mismatch')
    if count<1:raise ValueError('Positive sample count required')
    population=[];strata={};seen=set();columns=Counter();stratum_counts=Counter();total=0
    with gzip.open(path,'rb') as handle:
        for line in handle:
            if len(line)>1_000_000:raise ValueError('Oversized universe record')
            r=json.loads(line)
            if not isinstance(r,dict) or 'organisation_number' not in r:raise ValueError('Unsupported universe JSONL schema')
            columns.update(r.keys());total+=1
            org=canonical_organisation_number(r['organisation_number'])
            if org in seen:raise ValueError('Duplicate universe organisation')
            seen.add(org)
            if org in excluded:continue
            employees=r.get('employees')
            band='unknown' if not isinstance(employees,int) or isinstance(employees,bool) else '0-4' if employees<5 else '5-19' if employees<20 else '20-99' if employees<100 else '100+'
            group='|'.join((str(r.get('legal_form') or 'unknown'),str(r.get('industry_code') or 'unknown').split('.')[0],
                str(r.get('municipality_number') or 'unknown')[:2],band,'registered-url' if r.get('website') else 'no-registered-url'))
            stratum_counts[group]+=1
            rank=int(hashlib.sha256(f'{seed}|{org}'.encode()).hexdigest(),16)
            item=(-rank,org,group)
            if len(population)<count:heapq.heappush(population,item)
            elif rank < -population[0][0]:heapq.heapreplace(population,item)
            if group not in strata or rank<strata[group][0]:strata[group]=(rank,org,group)
    if len(population)<count:raise ValueError('Insufficient eligible candidates after exclusions')
    uniform=max(1,count*4//5)
    chosen=[(-rank,org,group,'population') for rank,org,group in sorted(population,reverse=True)[:uniform]]
    selected={x[1] for x in chosen}
    for rank,org,group in sorted(strata.values(),key=lambda x:hashlib.sha256(f'{seed}|stratum|{x[2]}'.encode()).hexdigest()):
        if len(chosen)==count:break
        if org not in selected:chosen.append((rank,org,group,'stratum_stress'));selected.add(org)
    for rank,org,group in sorted(population,reverse=True):
        if len(chosen)==count:break
        if org not in selected:chosen.append((-rank,org,group,'population'));selected.add(org)
    chosen.sort()
    batch=[{'organisation_number':org,'sample_slice':kind} for _,org,_,kind in chosen]
    manifest={'schema':'signalpost.coverage-selection/v1','universe_sha256':actual,'universe_rows':total,
        'actual_columns':dict(columns),'seed':seed,'requested':count,'selected':len(batch),
        'excluded_count':len(excluded),'selected_strata':dict(Counter(x[2] for x in chosen)),
        'eligible_strata':len(stratum_counts),'method':'80 percent smallest stable company hashes; remaining distinct stable strata minima; no source-success selection',
        'limitations':['Candidates only, not current legal identities. Metadata stress sample is not population-weighted recall.',
            'Universe has no known subunit/group membership, missing-record or same-name-collision ground truth; those require independent source/fixture strata.']}
    return batch,manifest

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--universe',type=Path,required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--count',type=int,default=100);p.add_argument('--seed',default='signalpost-coverage-20261020')
    p.add_argument('--exclude',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists() or a.manifest.exists():p.error('Selection outputs must be new')
    if a.output.resolve()==a.manifest.resolve():p.error('Output and manifest must differ')
    excluded=set(read_organisation_numbers(a.exclude)) if a.exclude else set()
    batch,manifest=select(a.universe,digest=a.sha256,count=a.count,seed=a.seed,excluded=excluded)
    atomic_jsonl(a.output,batch)
    manifest['batch_sha256']=hashlib.sha256(a.output.read_bytes()).hexdigest()
    if a.exclude:manifest['exclusion_sha256']=hashlib.sha256(a.exclude.read_bytes()).hexdigest()
    atomic_json(a.manifest,manifest)
    print(json.dumps({'selected':len(batch),'batch_sha256':manifest['batch_sha256'],'current_identity_verified':False}))
if __name__=='__main__':main()
