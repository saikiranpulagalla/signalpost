# Registry declaration source gate — 20 October 2026

Historical findings at21d6592. The subsequent offline registry-live extraction
and still-unapproved small pilot are documented in
[live declaration pilot plan](LIVE_DECLARATIONS_PILOT_PLAN.md). Statements below
about the old live normalizer describe that earlier candidate.

This extends local candidate10743a7. Submitted V2cf68573 remains the rollback.
No revision is recommended until genuine attributable incremental coverage is
measured. Synthetic declaration counts are not competition recall.

## What the evaluator actually supplies

The current [evaluation contract](https://builderr.ai/docs/signalpost-evaluation-harness.md),
under Official run requirements, explicitly promises a frozen registry snapshot
for identity anchoring. The latest downloaded Markdown is identical to the prior
copy: SHA-256 `b760becf3df406031c083209b4d9048ff9a9c9e27d2cfce182afcab8b28eb55f`.
It also supplies the company batch, cutoff, network policy and resource budget.
It does NOT publish the registry's exact format/schema, declaration population,
file digest, acquisition date or cutoff interpretation. Do not call those known.

* GUARANTEED: company batch and frozen identity registry; common run policy.
* PROVEN CODE: `--bulk` accepts content-detected plain/gzip CSV, exact-row claims;
  optional permitted enrichment needs explicit policy/authorization. No private
  prior profile/key is required for a fresh run.
* NOT DOCUMENTED: the supplied registry is this specific90-column public format,
  contains activity/purpose, or exposes a particular field-level cutoff policy.
* ORGANIZER CONFIGURATION: supplied format/path/provenance and approved run budget.

Thus the submitted command is not dependent on an entirely unpromised identity
file. CSV compatibility and declaration availability remain separate questions.
The public12-field universe is not a substitute for the promised registry.

## Three architectures

| Route | Benefit | Cost and boundary | Decision |
|---|---|---|---|
| A: supplied frozen registry | Zero new starts when literal declarations are supplied; keeps common identity baseline | Source columns/provenance must be checked; missing fields stay unknown | Preferred existing route; genuine declaration yield unmeasured |
| B: general public bulk snapshot | Potential batch-independent supplement amortized across companies | Actual completed acquisition, decoded size/indexing/RAM/refresh/cutoff acceptance must be proved | Acquisition attempted under explicit permission, incomplete; not deployable yet |
| C: existing registry-live lookup | Exact current company source, separate from frozen identity | One additional admitted start/company; current normalizer does not publish declarations; can displace finance/roles/locations | Compare offline; no default change or synthetic-only connector claim |

Current general-purpose source data is not hidden-batch precomputed research.
Nevertheless the contract only explicitly permits cached public-universe material
and requires external caches to be declared; it does not certify this proposed
bulk cache's cutoff treatment. Do not replace a historical identity snapshot with
current data or claim source acquisition time is a historical effective date.

## Actual authorized acquisition: unsuccessful, not a genuine coverage gain

The user separately authorized at most one HEAD+one GET to the official entity
CSV endpoint. No redirects, retries, authentication or per-company lookup. Caps:
512MiB compressed,4GiB decoded,300seconds acquisition,6GiB local storage.

Both requests returned HTTP200. HEAD declared155,098,913bytes, media type
`application/vnd.brreg.enhetsregisteret.enhet.v2+gzip;charset=UTF-8`, ETag
`3f07c1e3b869cf25a26b9bca2af9bfd7`, Last-Modified literal
`Tue Oct 20 04:28:29 CEST 2026`.
GET used that ETag as If-Match. Only20,971,520bytes were retained before the
absolute deadline check aborted; total process elapsed296.094seconds, exit1.
The child was reaped; an outer300-second kill/reap boundary was also in place.
No second attempt occurred. HTTP200 headers are not a complete download.

A complete header in the incomplete gzip prefix shows90columns, including
`aktivitet`, `vedtektsfestetFormaal`, business/postal address components,
`hjemmeside`, `epostadresse`, `telefon`, `mobil`, `stiftelsesdato`,
`registreringsdatoenhetsregisteret`, `erIKonsern` and classification/status fields.
This is observed prefix schema ONLY, not a validated full snapshot or population
measurement. Note the literal lower-case registration-date spelling; don't assume
every metadata alias is already projected. No prefix records are published.

Complete compressed SHA-256, decoded size, dataset row count, population, genuine
100-company declaration coverage, full parsing/indexing/runtime/RAM and completed
dataset transfer cost remain UNKNOWN. Partial-file hashes must not be relabelled
as the complete snapshot hash. ETag is not asserted to be SHA-256/source signing.
Raw bytes/headers including server cookies remain private and are not committed.

## Source rights and future acquisition

[BRREG documentation](https://data.brreg.no/enhetsregisteret/api/dokumentasjon/en/index.html)
links NLOD2.0 and documents bulk GET/HEAD, gzip, declaration columns and validators.
Attribution and applicable privacy restrictions remain required. General bulk
reuse is not blanket permission to publish unnecessary personal data. No raw
register is added to Git. Permission for this measurement is consumed; no retry,
resumed range request or additional company sampling is authorized implicitly.

The least costly next evidence is the actual organizer-supplied snapshot or an
already-lawfully-acquired complete source provided locally with provenance. Any
new acquisition requires separate approval informed by this failed300-second
measurement, not another guessed instantaneous-download assumption.

## Offline implementation and reproducibility

`scripts/measure_registry_snapshot.py` has NO network code. It requires a trusted
expected byte digest, explicit synthetic/organizer/current classification and a
new output directory. Current-source measurement also requires actual acquisition
metadata. These are local provenance checks, not cryptographic source-origin
authentication. The tool never creates/signs observations or rewrites input bytes.

It bounds compressed/decompressed bytes, row size and local duration; validates
strictCSV/UTF-8/gzip completion; checks nine-digit identities and duplicates across
all rows (including outside the sample); uses a disposable boundedSQLite uniqueness
index; rechecks the source digest/mtime after parsing; reports exact sample
membership, missing rows and declaration presence. Existing outputs are refused;
failed/interrupted work has no completed measurement. It does not provide a
self-attested reusable runtime index. Claims still come from the production
runner independently consuming the original pinned bytes.

Offline fixture example, after `uv sync --locked`, from repository root:

```powershell
$Snapshot = 'tests/v0/demo/frozen-registry.csv'
$Digest = (Get-FileHash -Algorithm SHA256 $Snapshot).Hash.ToLowerInvariant()
uv run --no-sync python -S scripts/measure_registry_snapshot.py --source $Snapshot --sha256 $Digest --organisations tests/v0/demo/organisations.jsonl --snapshot-kind synthetic --output-dir out/source-measurement
uv run --no-sync python -S scripts/run_competition_batch.py --organisations tests/v0/demo/organisations.jsonl --bulk $Snapshot --modules registry,accounting_obligation --network-policy offline --output-target starter_compatible --profiles-output out/source-demo/profiles.jsonl --output out/source-demo/envelopes.jsonl --report out/source-demo/report.json --viewer-output out/source-demo/viewer.html --run-id source-demo
```

This demo is synthetic and need not contain declarations. A genuine comparison
must bind the same independently pinned100company sample and complete authentic
snapshot to both V2 and candidate. No live fallback, no fabricated missing values,
and no invented cutoff are introduced. No changed runtime source priority is
recommended without measured genuine gain and accepted operational cost.
