#!/usr/bin/env python3
"""Measure a pinned LOCAL public/organizer/synthetic registry. Never downloads."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from norway_company_agent.registry_snapshot_measurement import measure
from v0_kernel.storage import atomic_json
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--organisations',type=Path,required=True)
    p.add_argument('--snapshot-kind',choices=('synthetic','organizer_frozen','current_public'),required=True)
    p.add_argument('--acquisition',type=Path);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--compressed-byte-cap',type=int,default=512*1024**2)
    p.add_argument('--decoded-byte-cap',type=int,default=4*1024**3)
    p.add_argument('--max-seconds',type=float,default=300)
    a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=False)
    atomic_json(a.output_dir/'status.json',{'status':'in_progress','network':'prohibited'})
    try:
        result=measure(a.source,a.organisations,a.sha256,kind=a.snapshot_kind,acquisition=a.acquisition,
            compressed_cap=a.compressed_byte_cap,decoded_cap=a.decoded_byte_cap,max_seconds=a.max_seconds,workspace=a.output_dir)
        atomic_json(a.output_dir/'measurement.json',result)
        atomic_json(a.output_dir/'status.json',{'status':'completed','source_sha256':result['source_sha256']})
        print(json.dumps({key:result[key] for key in ('requested','matched','selected_declarations','elapsed_seconds','external_requests')}))
    except Exception:
        atomic_json(a.output_dir/'status.json',{'status':'failed','network':'prohibited'});raise
if __name__=='__main__':main()
