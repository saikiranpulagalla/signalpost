# Signalpost — canonical submission guide

This is the **sole current installation and execution handoff**. Other root command guides are superseded; archived reports record earlier runs, not alternative commands.

Verified Revision 1 runtime baseline: `164065e488a265c3416560e79628e8ed831b1ed3`. Its completed regression gate passed **448/448 tests** (V0 93, V1 104, V2 251; zero skips), with locked Python 3.12 installation and exact-commit clean-checkout validation. See [P2 repair and verification scope](P2_REMEDIATION.md). This handoff correction is documentation-only and preserves that runtime; it does not claim a new regression or live benchmark. Pin the exact commit supplied with the revision handoff when installing.

Submitted V2 remains `cf6857371497a8a1b565715d1daacf7602e0749c`. The genuine 100-company live smoke used the earlier source commit `553e95835857396b282345e2e72d9f1d9e1ceaca`; it is historical evidence, not a live benchmark of this candidate. [Publication provenance](PUBLICATION_PROVENANCE.md) records the original public snapshot selection, not the current release gate.

Python **3.12**, locked `uv.lock`, no paid model key. Run from the project root. Batch size is derived from runtime input; do not hard-code expected-count.

## Install: choose Git or ZIP

Git route: use the repository below. For a pinned installation, set `$ReleaseCommit` to the exact public commit from the release handoff, then:

```powershell
git clone https://github.com/saikiranpulagalla/signalpost.git signalpost
Set-Location signalpost
git checkout $ReleaseCommit
uv sync --locked
```

ZIP route: verify its SHA-256 against the delivery checksum. Set `$ArchivePath` to that ZIP and `$ExtractionDirectory` to a new empty directory:

```powershell
Expand-Archive -LiteralPath $ArchivePath -DestinationPath $ExtractionDirectory
Set-Location $ExtractionDirectory
uv sync --locked
```

The ZIP has project files at its root and is not a Git checkout. Do not run Git checkout instructions inside it. Both routes install the same locked dependencies and use the same commands below. Installation may download declared packages; the sample's company-source transport remains strictly offline.

## Runnable zero-network sample

For the full regression commands and their writable temporary-directory prerequisite, see [publication reproduction](PUBLICATION_PROVENANCE.md#reproduction). That directory is generated locally, never copied from the development workspace or committed.

```powershell
uv run --no-sync python -S scripts/run_competition_batch.py --organisations tests/v0/demo/organisations.jsonl --bulk tests/v0/demo/frozen-registry.csv --modules registry,accounting_obligation --network-policy offline --output-target starter_compatible --profiles-output out/sample/profiles.jsonl --output out/sample/envelopes.jsonl --report out/sample/report.json --viewer-output out/sample/viewer.html --run-id sample
```

Use a fresh output directory. Open `out/sample/viewer.html` locally. This frozen synthetic demo proves installation/output, not genuine coverage. Do not add `--identity-mode`: the competition runner selects frozen replay by `--bulk`, and current-data bootstrap only by `--live-bootstrap` plus applicable authorization.

## Recommended primary scoring template — pending organizer configuration

Set only these caller/organizer inputs: `$BatchPath` (supplied JSONL), `$FrozenRegistryPath` (supplied matching frozen CSV, with pinned provenance), `$RunDirectory` (fresh writable directory for each batch), `$RunId` (unique identifier), `$RequestCap`, `$RuntimeSeconds` and `$ReserveSeconds` (approved/configured positive budgets with reserve below runtime). Execution is conditional on these inputs, organizer-approved resource limits, permission for public BRREG access and an agreed evidence-cutoff policy. Those limits and cutoff semantics are not established by this guide. Do not substitute a current download for the frozen registry. Two workers, ten-second source timeout and one attempt are project-tested choices, not official entitlements; reduce them if organizer policy requires. The explicit authorization flags below assert permission already granted; they do not obtain it. This template is not permission for another live run.

A fresh frozen-registry run requires no prior profiles, private signing key or private development file. It generates its own local observation-authentication key beside the report. Keep that key private and retain it only for authenticated local resume/refresh; do not include it in the evaluator's public artifacts. The required measured configuration is checked in at `config/brreg-admission-observed-20261013.json`. The proposed rich template below is conditional on permitted BRREG access and caller-supplied budgets; 90 minutes and 7,500 requests used in offline sensitivity tests are **not** official allowances or default handoff values.

```powershell
uv run --no-sync python -S scripts/run_competition_batch.py --organisations $BatchPath --bulk $FrozenRegistryPath --modules registry,accounting_obligation,financials,roles,locations,registry_live,group --source-priority financials,roles,locations,registry_live,group --source-allocation-policy core_breadth --source-latency-model config/brreg-admission-observed-20261013.json --source-family-budgets group=0 --optional-research-policy best_effort --network-policy authorized_public_brreg --confirm-authorized-live-sources --workers 2 --max-source-requests $RequestCap --source-timeout-seconds 10 --source-attempts 1 --max-runtime-seconds $RuntimeSeconds --finalization-reserve-seconds $ReserveSeconds --output-target starter_compatible --profiles-output "$RunDirectory/profiles.jsonl" --output "$RunDirectory/envelopes.jsonl" --report "$RunDirectory/report.json" --request-telemetry "$RunDirectory/telemetry.jsonl" --viewer-output "$RunDirectory/viewer.html" --run-id $RunId
```

Frozen identity is established first; batch-wide family waves are conservatively admitted. Optional per-family caps can be added with `--source-family-budgets`. Best effort retains valid supported facts when optional sources fail or block access. Admitted work stopped before a request remains `not_fetched` internally and appears as an explicit selected-but-unattempted diagnostic in terminal `errors`; it is never converted into checked absence or a new public availability state. Preplanned omissions remain unscheduled. Mandatory identity failures, corrupted evidence, invalid claims and normalization/integrity worker failures remain fatal. Successful publication still requires all workers to be joined and validation/finalization to finish within the overall budget. See [completion policy](OPTIONAL_RESEARCH_COMPLETION.md) and [common integrity boundary](SOURCE_INTEGRITY_CONSISTENCY.md).

**Explicit alternative:** replace `--optional-research-policy best_effort` with `--optional-research-policy strict` to require the existing fail-closed treatment of admitted but unattempted work. The CLI default remains strict; this recommended command opts into best effort. Neither mode guarantees completion under arbitrary source latency, filesystem failure or inadequate finalization time. A degraded completed run may receive credit only for its retained evidence; a shared-source outage is subject to Builderr's adjudication, not an automatic rerun inferred from either exit code.

This candidate separates the hard timeout from its measured admission forecast. The aggregate-only model uses saved per-family maxima with explicit margins, not a universal three-second assumption. `core_breadth` reserves financial/role coverage before residual waves. `group=0` is a disclosed resource-constrained proposal based on low historical positive yield, **not a finding that companies lack relationships**. Set a nonzero group cap only if the permitted budget justifies it. Default `family_waves` and timeout-based forecasts remain available by omitting the allocation/model options. Neither strategy guarantees unseen source latency; see [repair configuration evidence](REPAIR_PHASE_4.md). Best effort changes optional-source completion handling, not source priority, admission budgets or authorization.

The frozen-registry rich path retains evidence metadata and supported claims, but does not create raw-source captures. `--source-snapshots-dir` is available only for explicitly authorized `--live-bootstrap` runs; that identity mode cannot be combined with `--bulk`. Do not add the capture option to this frozen-registry template.

**Fallback, not the strongest competitive configuration:** replace modules with `registry,accounting_obligation`, use `--network-policy offline`, and remove source authorization/telemetry options. It exposes only supplied frozen facts and accounting interpretation, never automatically masquerades as completed rich research.

## Outputs and evidence

All paths below are relative to `$RunDirectory` in the rich command:

| Artifact | Role |
|---|---|
| `envelopes.jsonl` | **Primary evaluator output:** one ordered seven-key terminal envelope per requested company, with claims, evidence, changes, errors and request metadata. |
| `profiles.jsonl` | Supporting normalized observations, evidence-linked synthesis and authenticated history when supplied. |
| `viewer.html` | Supporting standalone viewer generated from these exact profiles: search, comparison, claim/source inspection and change history. |
| `report.json` | Supporting execution plan, request accounting, source outcomes and validation results. |
| `report.json.completion.json` | Supporting completion manifest binding exact envelope, profile and report bytes, ordered input, run identity and execution plan. |
| `telemetry.jsonl` | Supporting per-request metadata and failure diagnostics; no response bodies. Produced by the rich command, not the zero-network sample. |

**Successful publication requires process exit zero and verified completed bindings. File existence alone is insufficient:** failed runs can leave diagnostic or partial artifacts. The runner calls `norway_company_agent.completion.verify_completion` before committing the completed manifest; retained or copied artifacts should be checked with that verifier against their actual paths. Synthesis is not a standalone field in the seven-key envelope. Explicitly include the supporting profile/viewer paths in the evaluator handoff; automatic collection or scoring of these artifacts is not assumed.

A capture directory such as `permitted-sources/` belongs only to a separately configured, authorized live-bootstrap run (default 25MB cap), not the frozen-registry template above; even then, not every source byte is retained.

Keep observation-authentication keys private; never publish them. General saved export requires authoritative registry and authentic observations. Source links do not fetch until explicitly opened. Registered website metadata is not verified website content. No website/social/jobs crawling is enabled.

Refresh: use a new directory and add `--previous-profiles $PreviousProfiles --previous-bulk $PreviousFrozenRegistry`. Optional prior observations need trusted captured bytes or their private local observation key. Signed retained history additionally requires that private key, supplied with `--observation-key $PrivateKeyPath`; never distribute it. Authenticated last-known-good observations remain historical in `profiles.jsonl` and the viewer when current access fails; they do not become current financial claims. `--resume` is same-plan recovery, not refresh. Same snapshot creates no change. Builderr has not promised previous output-folder reuse; prior state must be supplied explicitly.

To regenerate a viewer containing authenticated history: `uv run --no-sync python -S scripts/build_profile_viewer.py --profiles "$RunDirectory/profiles.jsonl" --output "$RunDirectory/viewer.html" --observation-key $PrivateKeyPath`. Fresh profiles without history need no key option. The release command itself already generates the viewer.

## Genuine smoke, sources and costs

Retained public [genuine smoke report](release_artifacts/submission/HUNDRED_COMPANY_GENUINE_SMOKE_REPORT.md); source commit `553e95835857396b282345e2e72d9f1d9e1ceaca`: 100 exact identities, 500 requests, 3,046 claims, 605 complete metadata records, 264.112 seconds, direct reported cost $0. Not a new scheduler/presentation benchmark; not all metadata records imply retained raw bytes.

BRREG public entity, registered roles, subunit, annual-account and group endpoints; existing disclosures in `SOURCE_RIGHTS_AND_COST.md` and `release_artifacts/submission/V1_SOURCE_RIGHTS_COSTS.md` remain applicable. Entity register open-data/NLOD and annual-account source terms must be respected independently. No credentials or protected personal endpoints. Nominal full five-family enrichment is about five requests/company, not a requirement to exhaust the budget. Cost forecast uses admitted starts × declared per-request charge (public BRREG direct charges recorded as zero), plus declared infrastructure costs; latency extrapolation is not a guaranteed runtime.

## What remains organizer-controlled

Network/resource permissions, cutoff time semantics, output/profile/viewer/refresh handoff, treatment of degraded runs and precise accepted evidence/field representations remain organizer-controlled. Standalone synthesis is not inserted into the seven-key envelope. New supported claims may carry literal CSV-column or normalized-observation locators and known extraction methods; legacy observations can retain coarse source-family spans and explicitly unrecorded methods. None is advertised as an accepted literal source excerpt. The reserved `--evidence-cutoff-policy` argument currently refuses execution: it is not an implemented cutoff filter and must not be added as though it enforces a supplied policy. Establish compatibility with the organizer's actual cutoff requirements before live scoring; this guide supplies no invented interpretation. A complete unpublished JSON Schema is not itself mandatory; ask only about actual representation/handoff uncertainties. No official score or certification is claimed.

The current candidate adds the documented integrity, JSONL framing, optional-source completion and P2 repairs to the earlier V2 runtime. Registry declarations and former-name intervals are supported when authenticated source fields are available; they do not prove observed business operations or enable website, hiring or public-activity research. The [retained 20-company pilot report](REGISTRY_NAME_HISTORY_VALUE.md) documents limited genuine-source coverage, not nationwide prevalence or an official score. Admission still determines whether registry-live is reached. Unit-less financial comparisons remain withheld; no scale was invented. This documentation correction adds no browser/mobile, Linux or live-source verification. Repository publication, emails and submission require their respective explicit authorization.

## Separate handoff materials

The five organizer drafts from the private development workspace are excluded, together with superseded command guides and audit history. Original local files remain untouched. This canonical guide, not older private handoffs, defines current commands. Historical source commits named above are provenance identifiers; their private Git history is not copied into this public repository.
