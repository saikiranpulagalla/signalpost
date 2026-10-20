"""Offline category metrics, not Builderr's hidden ground truth or official score."""
from __future__ import annotations
from collections import Counter
import json,math
from pathlib import Path
from .batch import canonical_organisation_number
from .profile_integrity import validate_metadata,verify_baseline,verify_claims,verify_source_record,verify_receipt,verify_history
from .research_result import build_research_result

EVIDENCE_CLASSES=frozenset({'historical_retained','authorized_new','synthetic','virtual_time'})
FAMILIES=('identity_status','registered_business_declarations','financials','leadership','registered_locations',
          'corporate_relationships','website_metadata','verified_website_content','contact','hiring','dated_activity')

def family(field: str) -> str | None:
    if field.startswith('financial_'):return 'financials'
    return {'legal_name':'identity_status','legal_form':'identity_status','legal_status':'identity_status',
        'registered_legal_status':'identity_status','industry':'identity_status','employee_count':'identity_status',
        'registered_municipality':'identity_status','accounting_obligation':'identity_status',
        'registry_latest_filed_year':'identity_status','entity_register_registration_date':'identity_status',
        'incorporation_date':'identity_status','registered_activity':'registered_business_declarations',
        'registered_purpose':'registered_business_declarations','registered_role':'leadership',
        'registered_address':'registered_locations','registered_subunit':'registered_locations',
        'corporate_relationship':'corporate_relationships','registered_website':'website_metadata',
        'official_website':'verified_website_content','website_description':'verified_website_content',
        'registered_email':'contact','registered_phone':'contact','contact_email':'contact','contact_phone':'contact',
        'job_posting':'hiring','dated_activity':'dated_activity'}.get(field)

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def claim_key(claim):
    return canonical([claim.get('field'),claim.get('value'),claim.get('reporting_period'),claim.get('scope')])

def export_key(claim,internal=False):
    value=claim.get('value')
    if internal and isinstance(value,dict) and isinstance(claim.get('reporting_period'),dict) and claim.get('scope'):
        value={**value,'reporting_period':claim['reporting_period'],'scope':claim['scope']}
    return canonical([claim.get('field'),value])

def telemetry_metrics(rows:list[dict],expected:list[str]) -> dict:
    """One run only; physical starts, rejected operations and missing times differ."""
    if len({r.get('run_id') for r in rows})>1:raise ValueError('Do not merge telemetry from different runs')
    if any(r.get('organisation_number') not in expected for r in rows):raise ValueError('Telemetry outside supplied company batch')
    starts=[r for r in rows if isinstance(r.get('physical_source_request_number'),int)]
    numbers=[r['physical_source_request_number'] for r in starts]
    if len(numbers)!=len(set(numbers)):raise ValueError('Duplicate physical request sequence')
    families={}
    for f in sorted({str(r.get('source_family')) for r in rows}):
        source=[r for r in starts if str(r.get('source_family'))==f]
        timings=sorted(r['duration_ms'] for r in source if isinstance(r.get('duration_ms'),(int,float)) and
            not isinstance(r['duration_ms'],bool) and math.isfinite(r['duration_ms']) and r['duration_ms']>=0)
        families[f]={'physical_starts':len(source),'http_status_counts':dict(Counter(str(r.get('http_status')) for r in source)),
            'classifications':dict(Counter(str(r.get('classification')) for r in source)),
            'bytes_received':sum(r.get('bytes_received',0) for r in source),
            'timing_samples':len(timings),'missing_timings':len(source)-len(timings),
            **{name:(timings[max(0,math.ceil(len(timings)*q)-1)] if timings else None)
               for name,q in (('p50_ms',.5),('p90_ms',.9),('p95_ms',.95),('p99_ms',.99))}}
    return {'physical_starts':len(starts),'rejected_or_cache_operations':len(rows)-len(starts),'families':families,
        'limit':'Elapsed request samples are not wall-clock throughput; killed in-flight operations can lack telemetry.'}

def evaluate(profiles:list[dict],envelopes:list[dict],expected:list[str],*,evidence_class:str,
             baselines:dict[str,dict]|None=None,key:bytes|None=None,source_dir:Path|None=None) -> dict:
    if evidence_class not in EVIDENCE_CLASSES:raise ValueError('Explicit, separate evidence class required')
    expected=[canonical_organisation_number(o) for o in expected]
    if len(expected)!=len(set(expected)):raise ValueError('Duplicate expected organisations')
    p_orgs=[p.get('organisation_number') for p in profiles]; e_orgs=[e.get('organisation_number') for e in envelopes]
    if p_orgs!=expected or e_orgs!=expected:raise ValueError('Ordered membership differs from supplied batch')
    matrix=[];issues=[];distinct=set();duplicates=0;lost=0;extra=0;metadata_total=0;replay_total=0
    citation_errors=0;sentences=0;cited=0;declared_changes=0;authenticated_changes=0
    outcomes=Counter();proof_totals=Counter()
    for p,envelope in zip(profiles,envelopes):
        org=p['organisation_number'];records=p.get('evidence',{});claims=p.get('research_result',{}).get('claims',[])
        declared=Counter();replay=Counter();proof={};valid_record={}
        for f,r in records.items():
            outcomes[f+':'+str(r.get('status'))]+=1
            try:validate_metadata({'organisation_number':org,'evidence':{f:r}});valid_record[f]=True
            except (ValueError,TypeError) as exc:
                issues.append({'organisation_number':org,'source_family':f,'kind':'invalid_metadata','reason':str(exc)});valid_record[f]=False
            if r.get('status')!='available' or not valid_record[f]:continue
            if baselines and org in baselines and f in ('registry','accounting_obligation'):
                try:verify_baseline(p,baselines[org]);proof[f]='supplied_frozen_bytes'
                except ValueError as exc:issues.append({'organisation_number':org,'kind':'baseline_mismatch','reason':str(exc)})
            if f not in proof and verify_receipt(p,f,key):proof[f]='local_authenticated_observation'
            if f not in proof:
                try:
                    if verify_source_record(p,f,source_dir):proof[f]='captured_bytes_match_normalization'
                except (ValueError,TypeError,KeyError) as exc:issues.append({'organisation_number':org,'source_family':f,'kind':'source_replay_mismatch','reason':str(exc)})
        proof_totals.update(proof.values())
        projected={claim_key(c):c['id'] for c in build_research_result(p)['claims']}
        try:verify_claims(p);projection_consistent=True
        except (ValueError,TypeError):projection_consistent=False
        for c in claims:
            f=family(str(c.get('field','')))
            if f:declared[f]+=1
            identity=(org,claim_key(c))
            if identity in distinct:duplicates+=1
            distinct.add(identity)
            refs=c.get('evidence_fields',[])
            metadata_ok=bool(refs) and all(valid_record.get(r) and records[r].get('status')=='available' and
                all(records[r].get(k) for k in ('source_url','retrieved_at','content_sha256')) for r in refs)
            metadata_total+=metadata_ok
            replayable=metadata_ok and all(r in proof for r in refs) and projected.get(claim_key(c))==c.get('id')
            replay_total+=bool(replayable)
            if f and replayable:replay[f]+=1
        expected_export=Counter(export_key(c,True) for c in claims)
        actual_export=Counter(export_key(c) for c in envelope.get('claims',[]) if c.get('availability')=='available')
        lost+=sum((expected_export-actual_export).values());extra+=sum((actual_export-expected_export).values())
        ev_ids={e.get('id') for e in envelope.get('evidence',[])}
        citation_errors+=sum(c.get('availability')=='available' and (not c.get('evidence_ids') or not set(c['evidence_ids'])<=ev_ids) for c in envelope.get('claims',[]))
        ids={c.get('id') for c in claims};changes={c.get('id') for c in p.get('changes',[])}
        for s in p.get('synthesis',{}).get('sentences',[]):
            sentences+=1;cited+=bool(s.get('claim_ids'))
            citation_errors+=not set(s.get('claim_ids',[]))<=ids or not set(s.get('change_ids',[]))<=changes
        declared_changes+=len(p.get('changes',[]))
        try:verify_history(p,key);authenticated_changes+=len(p.get('changes',[]))
        except (ValueError,TypeError):pass
        matrix.append({'organisation_number':org,'declared_positive_facts':dict(declared),
            'independently_replayable_facts':dict(replay),'proof_by_source':proof,
            'current_projection_consistent':projection_consistent})
    return {'schema':'signalpost.coverage-benchmark/v1','evidence_class':evidence_class,'companies':len(expected),
        'company_family_coverage':{f:{'declared_positive_companies':sum(r['declared_positive_facts'].get(f,0)>0 for r in matrix),
            'independently_replayable_companies':sum(r['independently_replayable_facts'].get(f,0)>0 for r in matrix),
            'declared_facts':sum(r['declared_positive_facts'].get(f,0) for r in matrix),
            'independently_replayable_facts':sum(r['independently_replayable_facts'].get(f,0) for r in matrix)} for f in FAMILIES},
        'distinct_declared_facts':len(distinct),'duplicate_declared_facts':duplicates,
        'metadata_complete_claims':metadata_total,'independently_replayable_claims':replay_total,
        'source_proof_counts':dict(proof_totals),'source_outcomes':dict(outcomes),'export_missing_facts':lost,'export_extra_facts':extra,
        'synthesis_sentences':sentences,'sentences_with_claim_citations':cited,'invalid_citation_references':citation_errors,
        'declared_change_events':declared_changes,'authenticated_change_events':authenticated_changes,
        'issues':issues,'company_matrix':matrix,
        'limitations':['No official score or recall/precision estimate: independent ground truth is not supplied.',
            'Declared metadata, captured bytes and local authentication are different support levels.',
            'Byte hashes attest content consistency, not a cryptographic source-origin signature.',
            'Citation resolution does not independently prove sentence entailment.',
            'Synthetic and virtual-time rows never establish genuine company coverage.']}
