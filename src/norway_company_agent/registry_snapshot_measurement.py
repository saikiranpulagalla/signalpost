"""Bounded, network-free admission measurement of caller-supplied registry bytes.

This does not authenticate BRREG origin, download data, manufacture receipts,
change historical cutoffs or provide an unverified cache to the batch runner.
The ordinary runner consumes the same pinned source bytes independently.
"""
from __future__ import annotations
import csv,gzip,hashlib,io,json,sqlite3,tempfile,time
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from .batch import canonical_organisation_number,read_organisation_numbers
from .sampling import registered_declaration

SOURCE='https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv'
class LimitedReader(io.RawIOBase):
    def __init__(self,source,limit,check):self.source=source;self.limit=limit;self.check=check;self.count=0
    def readable(self):return True
    def readinto(self,buffer):
        self.check();data=self.source.read(min(len(buffer),65536,self.limit-self.count+1));self.count+=len(data)
        if self.count>self.limit:raise ValueError('Decoded snapshot byte ceiling exceeded')
        buffer[:len(data)]=data;self.check();return len(data)

def measure(source:Path,batch:Path,expected_sha256:str,*,kind:str,acquisition:Path|None=None,
            compressed_cap:int=512*1024**2,decoded_cap:int=4*1024**3,max_seconds:float=300,
            workspace:Path|None=None,clock=time.monotonic)->dict:
    if kind not in {'synthetic','organizer_frozen','current_public'}:raise ValueError('Explicit snapshot kind required')
    if min(compressed_cap,decoded_cap,max_seconds)<=0:raise ValueError('Positive resource ceilings required')
    start=clock();initial_stat=source.stat()
    def check():
        if clock()-start>=max_seconds:raise TimeoutError('Local snapshot measurement deadline exceeded')
    if source.stat().st_size>compressed_cap:raise ValueError('Source exceeds compressed byte ceiling')
    expected_sha256=expected_sha256.lower()
    if len(expected_sha256)!=64 or any(c not in '0123456789abcdef' for c in expected_sha256):raise ValueError('Pinned source SHA-256 required')
    digest=hashlib.sha256()
    with source.open('rb') as f:
        for chunk in iter(lambda:f.read(65536),b''):check();digest.update(chunk)
    if digest.hexdigest()!=expected_sha256:raise ValueError('Snapshot substitution or corruption: SHA-256 mismatch')
    provenance=None
    if acquisition:
        provenance=json.loads(acquisition.read_text(encoding='utf-8'))
        if provenance.get('sha256')!=expected_sha256 or provenance.get('effective_url')!=SOURCE or provenance.get('status')!=200:
            raise ValueError('Acquisition attribution/digest/status mismatch')
        instants={}
        for field in ('started_at','completed_at'):
            stamp=datetime.fromisoformat(provenance[field].replace('Z','+00:00'))
            if stamp.utcoffset() is None or stamp>datetime.now(timezone.utc):raise ValueError('Invalid acquisition time')
            instants[field]=stamp
        if instants['completed_at']<instants['started_at']:raise ValueError('Reversed acquisition interval')
    if kind=='current_public' and not provenance:raise ValueError('Current public measurement requires acquisition metadata')
    wanted=read_organisation_numbers(batch);wanted_set=set(wanted);selected={};population={'activity':0,'purpose':0,'both':0,'neither':0}
    @contextmanager
    def text():
        with source.open('rb') as raw:
            magic=raw.read(2);raw.seek(0)
            stream=gzip.GzipFile(fileobj=raw) if magic==b'\x1f\x8b' else raw
            bounded=LimitedReader(stream,decoded_cap,check)
            with io.TextIOWrapper(io.BufferedReader(bounded),encoding='utf-8-sig',newline='') as handle:
                yield handle,bounded
    with text() as (handle,_):
        sample=handle.read(8192)
        try:dialect=csv.Sniffer().sniff(sample,delimiters=';,\t')
        except csv.Error as exc:raise ValueError('Unrecognized registry CSV delimiter') from exc
    prior_limit=csv.field_size_limit();csv.field_size_limit(1_000_000)
    try:
        with tempfile.TemporaryDirectory(prefix='registry-measure-',dir=workspace) as temporary:
            db=sqlite3.connect(str(Path(temporary)/'identities.sqlite'))
            try:
                db.execute('pragma page_size=4096');db.execute('pragma cache_size=-4096');db.execute('pragma journal_mode=off')
                db.execute('pragma max_page_count=262144')  # At most 1GiB disposable index with default4KiB pages.
                db.execute('create table identities (org text primary key) without rowid')
                with text() as (handle,bounded):
                    reader=csv.DictReader(handle,dialect=dialect,strict=True)
                    columns=reader.fieldnames
                    if not columns or len(set(columns))!=len(columns) or any(not c.strip() for c in columns):raise ValueError('Invalid registry headers')
                    if 'organisasjonsnummer' not in columns or 'navn' not in columns:raise ValueError('Unexpected registry identity schema')
                    rows=0
                    for row in reader:
                        check()
                        if None in row or any(v is None for v in row.values()):raise ValueError('Malformed registry row')
                        if sum(len(v) for v in row.values())>1_000_000:raise ValueError('Registry row byte budget exceeded')
                        org=canonical_organisation_number(row['organisasjonsnummer'])
                        if row['organisasjonsnummer']!=org:raise ValueError('Noncanonical registry identifier')
                        try:db.execute('insert into identities values (?)',(org,))
                        except sqlite3.IntegrityError as exc:raise ValueError('Duplicate registry organisation number') from exc
                        a=registered_declaration(row.get('aktivitet')) is not None
                        p=registered_declaration(row.get('vedtektsfestetFormaal')) is not None
                        population['activity']+=a;population['purpose']+=p;population['both']+=a and p;population['neither']+=not(a or p)
                        if org in wanted_set:selected[org]={'activity':a,'purpose':p}
                        rows+=1
                        if rows%10000==0:db.commit()
                    decoded_bytes=bounded.count
                db.commit();index_bytes=(Path(temporary)/'identities.sqlite').stat().st_size
            finally:db.close()
    finally:csv.field_size_limit(prior_limit)
    check()
    final_digest=hashlib.sha256()
    with source.open('rb') as f:
        for chunk in iter(lambda:f.read(65536),b''):check();final_digest.update(chunk)
    final_stat=source.stat()
    if (final_digest.hexdigest()!=expected_sha256 or initial_stat.st_size!=final_stat.st_size or
            initial_stat.st_mtime_ns!=final_stat.st_mtime_ns):raise ValueError('Snapshot changed during measurement')
    return {'schema':'signalpost.registry-measurement/v1','snapshot_kind':kind,'source_sha256':expected_sha256,
        'source_bytes':source.stat().st_size,'decoded_bytes':decoded_bytes,'columns':columns,'population_rows':rows,
        'population_declarations':population,'input_sha256':hashlib.sha256(batch.read_bytes()).hexdigest(),
        'requested':len(wanted),'matched':len(selected),'missing':[o for o in wanted if o not in selected],
        'company_matrix':[{'organisation_number':o,**selected.get(o,{'activity':False,'purpose':False}),
                           'present_in_snapshot':o in selected} for o in wanted],
        'selected_declarations':{key:sum(r[key] for r in selected.values()) for key in ('activity','purpose')},
        'selected_both':sum(r['activity'] and r['purpose'] for r in selected.values()),
        'selected_neither':sum(not(r['activity'] or r['purpose']) for r in selected.values()),
        'identity_uniqueness_index_bytes':index_bytes,'elapsed_seconds':clock()-start,'external_requests':0,
        'acquisition':({key:provenance.get(key) for key in ('effective_url','started_at','completed_at','bytes','sha256')} if provenance else None),
        'limitations':['Local content verification, not cryptographic source-origin attestation.',
            'Current snapshot is not a substitute for an organizer historical snapshot/cutoff.',
            'Registered declarations are not observed operations or an official scoring family.',
            'SQLite is disposable uniqueness checking, not a trusted reusable runtime cache.']}
