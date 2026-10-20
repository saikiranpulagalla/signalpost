# Competitive V3 — reproducible experiments, not an official score

Rollback/submitted source: `cf6857371497a8a1b565715d1daacf7602e0749c`.
Local branch: `codex/signalpost-competitive-v3`. No publication authorized.

## Evidence classes and acceptance

Always run `scripts/evaluate_coverage.py` with ONE explicit class:
`historical_retained`, `authorized_new`, `synthetic` or `virtual_time`. Never
combine these into a score. The tool reports declared metadata, current
projection consistency, captured-byte/local-receipt/frozen-byte support,
company-level family coverage, duplicate facts, export loss, citation resolution
and optional telemetry separately. It has no hidden ground truth and cannot
estimate official recall, factual precision, qualification or ranking.

Canonical facts include organisation, field, value, period and entity scope.
This local taxonomy is not asserted to be Builderr's scoring taxonomy. Alias
fields may still describe the same underlying reality. Citation references are
checked; natural-language entailment is not thereby proved.

## Pinned independent sample

Use the actual retained public JSONL gzip, not a synthetic registry, with archive
SHA-256 `1c89710e5b01f8617e86d09fbdff4a52f2f8dbbba297e74f7164b5984f5a0384`.
The selected batch has 80 stable-hash population choices plus 20 distinct-stratum
stress choices, excluding the historical 100 if their input file is supplied.
Strata use actual available legal form, industry division, municipality prefix,
employee band and registered-URL presence. No successful-source filtering.
Missing subunit/group/collision/filing-age strata are UNKNOWN, not assumed.
Results are not population-weighted recall. A separate synthetic collision and
failure corpus remains necessary.

```powershell
python -S scripts/select_coverage_sample.py --universe "$UniverseArchive" --sha256 1c89710e5b01f8617e86d09fbdff4a52f2f8dbbba297e74f7164b5984f5a0384 --count 100 --exclude "$HistoricalBatch" --output out/independent-candidates.jsonl --manifest out/independent-selection.json
```

Default seed is `signalpost-coverage-20261020`. Outputs must be new. Candidates
are eligible-universe members, NOT current live-verified company records.
For the locally retained historical exclusion file with SHA-256
`dc037f825ffbb37f8b7d4b85b033d0e99bcfef53b445dcde7b06d809214523d8`,
the independently selected input SHA-256 is
`b5d5e759a25bedf79951041ff3866104b67ea0797d7ae83d5cc988b7270fbfac`.
The universe and private historical observations are not bundled runtime data.

## Evaluate a completed local run

```powershell
python -S scripts/evaluate_coverage.py --organisations "$BatchPath" --profiles "$RunDirectory/profiles.jsonl" --envelopes "$RunDirectory/envelopes.jsonl" --bulk "$FrozenRegistryPath" --observation-key "$RunDirectory/report.json.observation-auth.key" --report "$RunDirectory/report.json" --telemetry "$RunDirectory/telemetry.jsonl" --evidence-class synthetic --output "$RunDirectory/coverage.json"
```

Class `synthetic` above is for fixture observations only. Use `authorized_new`
ONLY for a separately authorized genuine run. For historical retained metadata,
omit unavailable baseline/key/report and supply only actually captured files
with `--verified-source-snapshots`. The key is optional, private, never emitted
in the report, and never created by this verifier. An absent key cannot be
replaced by two mutually consistent files. Modern `--report` invokes full
completion verification; do not assert those newer bindings for old manifests.

## Two-checkout actual-CLI comparison

Install Python 3.12 and `uv sync --locked`. Prepare a separate exact baseline
checkout. From this candidate checkout:

```powershell
python -S scripts/benchmark_registry_declarations.py --baseline "$BaselineCheckout" --candidate . --output-dir out/declaration-comparison
```

Defaults: 100/1,000/1,100/1,500 companies, hypothetical 5/10/20/45/90 minutes,
two workers, 7,500 fixture starts maximum, 120-second reserve, checked-in measured
model, `core_breadth`, group=0. These are sensitivity conditions, NOT official
allowances. Actual subprocesses run the production scheduler, normalization,
adapter, storage and viewer; the permanent test transport replaces HTTP and
forbids external sockets. Fixture time uses saved maxima times 1.25 with ideal
two-worker overlap; startup/serialization wall time is measured separately, not
claimed as simulated network latency or a guaranteed deadline.

Both checkouts receive IDENTICAL synthetic CSV bytes and inputs. Activity exists
for all fixture rows; purpose is blank in every twentieth row. This deliberately
controlled distribution is not measured genuine prevalence. Results compare
admitted families, requests, declared/replayable facts, citations, terminal
membership, hashes, artifact bytes and actual wall time. No private hooks enter
the official command. Default test output contains local fixture keys and must
not be published wholesale.

Expected mechanism: supported literal frozen declarations add facts at zero
extra source starts even when registry-live is omitted. Expected zero gain when
the supplied CSV lacks these columns. Genuine incremental coverage remains
UNVERIFIED until an authoritative representative CSV is supplied or separately
authorized observations prove the complete relevant source path.

## Candidate evaluator handoff (not a submitted revision)

No new source flags, families or permissions are needed. Use a fresh directory
and unique ID per scheduled batch. Caller must bind the actual supplied batch,
matching frozen registry and approved request/runtime/reserve values. Public
BRREG authorization is required for this rich configuration:

```powershell
uv run --no-sync python -S scripts/run_competition_batch.py --organisations "$BatchPath" --bulk "$FrozenRegistryPath" --modules registry,accounting_obligation,financials,roles,locations,registry_live,group --source-priority financials,roles,locations,registry_live,group --source-allocation-policy core_breadth --source-latency-model config/brreg-admission-observed-20261013.json --source-family-budgets group=0 --network-policy authorized_public_brreg --confirm-authorized-live-sources --workers 2 --max-source-requests $RequestCap --source-timeout-seconds 10 --source-attempts 1 --max-runtime-seconds $RuntimeSeconds --finalization-reserve-seconds $ReserveSeconds --output-target starter_compatible --profiles-output "$RunDirectory/profiles.jsonl" --output "$RunDirectory/envelopes.jsonl" --report "$RunDirectory/report.json" --request-telemetry "$RunDirectory/telemetry.jsonl" --viewer-output "$RunDirectory/viewer.html" --run-id "$RunId"
```

Safe fallback remains the canonical guide's registry/accounting-only offline
command. Generated profiles contain cited synthesis; envelopes retain seven
keys; viewer derives solely from those profiles. Sidecar pickup and cutoff
semantics still require organizer configuration. No prior-folder reuse assumed.
No automatic conversion of `not_fetched` or failed rich research into success.
