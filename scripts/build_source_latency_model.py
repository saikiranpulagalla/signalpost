#!/usr/bin/env python3
"""Build an aggregate-only, conditional admission forecast from saved telemetry."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path

FAMILIES = ('financials', 'roles', 'locations', 'registry_live', 'group')


def build_model(rows: list[dict], hard_timeout: float = 10.0) -> dict:
    if not math.isfinite(hard_timeout) or hard_timeout <= 0:
        raise ValueError('Positive hard timeout required')
    families = {}
    for family in FAMILIES:
        observed = [r for r in rows if r.get('source_family') == family and
                    r.get('physical_source_request_number') is not None and not r.get('cache_hit')]
        measured, failed, unmeasured = [], 0, 0
        statuses = {}
        for row in observed:
            status = str(row.get('http_status'))
            statuses[status] = statuses.get(status, 0) + 1
            duration = row.get('duration_ms')
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration >= 0:
                measured.append(duration / 1000)
            else:
                unmeasured += 1
            if row.get('classification') not in {'success', 'not_found'}:
                failed += 1
        measured.sort()
        def quantile(q):
            return measured[max(0, math.ceil(len(measured) * q) - 1)] if measured else None
        tail = max(measured, default=0)
        if not observed or failed or unmeasured:
            tail = max(tail, hard_timeout)
        # An explicit engineering margin, not a quantile guarantee or source SLA.
        forecast = max(1.0, math.ceil((tail * 1.5 + .25) * 2) / 2)
        families[family] = {'physical_samples': len(observed), 'failed_or_incomplete_samples': failed,
            'unmeasured_samples': unmeasured, 'http_statuses': statuses,
            'p50_seconds': quantile(.5), 'p95_seconds': quantile(.95), 'p99_seconds': quantile(.99),
            'max_seconds': max(measured, default=None), 'forecast_seconds': forecast}
    return {'schema': 'signalpost.admission-latencies/v1', 'families': families,
        'hard_timeout_reference_seconds': hard_timeout,
        'forecast_rule': 'ceil_to_half_second(max_observed*1.5+0.25), minimum 1s; failures/missing timing include hard timeout before margin',
        'limitations': 'Conditional forecast, not a hard bound or official budget. No guarantee for unseen tails. Rate limiting stops the service; it is not hidden in a latency average.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--telemetry',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--hard-timeout-seconds',type=float,default=10)
    args=parser.parse_args()
    raw=args.telemetry.read_bytes()
    result=build_model([json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()],args.hard_timeout_seconds)
    result['telemetry_sha256']=hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps({f:r['forecast_seconds'] for f,r in result['families'].items()},sort_keys=True))


if __name__=='__main__':main()
