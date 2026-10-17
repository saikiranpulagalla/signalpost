# Signalpost — canonical submission guide

This is the **sole current installation and execution handoff**. Other root command guides are superseded; archived reports record earlier runs, not alternative commands.

Original runtime candidate: `8ffe82d01ff5c2182d32e187e9444f680e3457ed`, previously verified 342/342 tests. Publication source: `10c9ccabd3915be1b7d758b57e9500c3a4def1c7` (documentation-only successor). This public repository has independent clean history. See [publication provenance](PUBLICATION_PROVENANCE.md) for file selection and clean-clone verification; no new live benchmark is claimed.

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

Set only these caller/organizer inputs: `$BatchPath` (supplied JSONL), `$FrozenRegistryPath` (supplied matching frozen CSV, with pinned provenance), `$RunDirectory` (fresh writable directory), `$RunId` (unique identifier), `$RequestCap`, `$RuntimeSeconds` and `$ReserveSeconds` (approved/configured positive budgets with reserve below runtime). Request/time limits and cutoff semantics are NOT yet supplied. Do not substitute a current download for the frozen registry. Two workers, ten-second source timeout and one attempt are project-tested conservative choices, not claimed official entitlements; reduce them if organizer policy requires. Applicable public-BRREG authorization is a prerequisite. This template is not permission for another live run.

```powershell
uv run --no-sync python -S scripts/run_competition_batch.py --organisations $BatchPath --bulk $FrozenRegistryPath --modules registry,accounting_obligation,financials,roles,locations,registry_live,group --source-priority financials,roles,locations,registry_live,group --source-allocation-policy core_breadth --source-latency-model config/brreg-admission-observed-20261013.json --source-family-budgets group=0 --network-policy authorized_public_brreg --confirm-authorized-live-sources --workers 2 --max-source-requests $RequestCap --source-timeout-seconds 10 --source-attempts 1 --max-runtime-seconds $RuntimeSeconds --finalization-reserve-seconds $ReserveSeconds --output-target starter_compatible --profiles-output "$RunDirectory/profiles.jsonl" --output "$RunDirectory/envelopes.jsonl" --report "$RunDirectory/report.json" --request-telemetry "$RunDirectory/telemetry.jsonl" --viewer-output "$RunDirectory/viewer.html" --run-id $RunId
```

Frozen identity is established first; batch-wide family waves are conservatively admitted. Optional per-family caps can be added with `--source-family-budgets`. Not selected ≠ checked absent; unexpectedly unfinished admitted work can fail closed. Conservative admission does not guarantee third-party latency.

This repair candidate separates the hard timeout from its measured admission forecast. The aggregate-only model uses saved per-family maxima with explicit margins, not a universal three-second assumption. `core_breadth` reserves financial/role coverage before residual waves. `group=0` is a disclosed resource-constrained proposal based on low historical positive yield, **not a finding that companies lack relationships**. Set a nonzero group cap only if the permitted budget justifies it. Default `family_waves` and timeout-based forecasts remain available by omitting the allocation/model options. Neither strategy guarantees unseen source latency; see [repair configuration evidence](REPAIR_PHASE_4.md). An unexpectedly unfinished admitted operation still fails closed.

The frozen-registry rich path retains evidence metadata and supported claims, but does not create raw-source captures. `--source-snapshots-dir` is available only for explicitly authorized `--live-bootstrap` runs; that identity mode cannot be combined with `--bulk`. Do not add the capture option to this frozen-registry template.

**Fallback, not the strongest competitive configuration:** replace modules with `registry,accounting_obligation`, use `--network-policy offline`, and remove source authorization/telemetry options. It exposes only supplied frozen facts and accounting interpretation, never automatically masquerades as completed rich research.

## Outputs and evidence

`envelopes.jsonl`: seven starter keys, ordered requested identities, facts/evidence/change/error/request metadata. `profiles.jsonl`: normalized observations and deterministic synthesis. `viewer.html`: search, comparison, fact citations and old/new change evidence. `report.json` plus `.completion.json`: local whole-run completion bindings. `telemetry.jsonl`: request metadata, not response bodies. A capture directory such as `permitted-sources/` belongs only to a separately configured, authorized live-bootstrap run (default 25MB cap), not the frozen-registry template above; even then, not every source byte is retained.

Keep observation-authentication keys private; never publish them. General saved export requires authoritative registry and authentic observations. Source links do not fetch until explicitly opened. Registered website metadata is not verified website content. No website/social/jobs crawling is enabled.

Refresh: use a new directory and add `--previous-profiles $PreviousProfiles --previous-bulk $PreviousFrozenRegistry`. Optional prior observations need trusted captured bytes or their private local observation key. Signed retained history additionally requires that private key, supplied with `--observation-key $PrivateKeyPath`; never distribute it. Authenticated last-known-good observations remain historical in `profiles.jsonl` and the viewer when current access fails; they do not become current financial claims. `--resume` is same-plan recovery, not refresh. Same snapshot creates no change. Builderr has not promised previous output-folder reuse; prior state must be supplied explicitly.

To regenerate a viewer containing authenticated history: `uv run --no-sync python -S scripts/build_profile_viewer.py --profiles "$RunDirectory/profiles.jsonl" --output "$RunDirectory/viewer.html" --observation-key $PrivateKeyPath`. Fresh profiles without history need no key option. The release command itself already generates the viewer.

## Genuine smoke, sources and costs

Retained public [genuine smoke report](release_artifacts/submission/HUNDRED_COMPANY_GENUINE_SMOKE_REPORT.md); source commit `553e95835857396b282345e2e72d9f1d9e1ceaca`: 100 exact identities, 500 requests, 3,046 claims, 605 complete metadata records, 264.112 seconds, direct reported cost $0. Not a new scheduler/presentation benchmark; not all metadata records imply retained raw bytes.

BRREG public entity, registered roles, subunit, annual-account and group endpoints; existing disclosures in `SOURCE_RIGHTS_AND_COST.md` and `release_artifacts/submission/V1_SOURCE_RIGHTS_COSTS.md` remain applicable. Entity register open-data/NLOD and annual-account source terms must be respected independently. No credentials or protected personal endpoints. Nominal full five-family enrichment is about five requests/company, not a requirement to exhaust the budget. Cost forecast uses admitted starts × declared per-request charge (public BRREG direct charges recorded as zero), plus declared infrastructure costs; latency extrapolation is not a guaranteed runtime.

## What remains organizer-controlled

Network/resource permissions, cutoff time semantics, output/profile/viewer/refresh handoff, exit-code treatment and precise accepted evidence/field representations. Standalone synthesis is not inserted into the seven-key envelope. `claim_span` is currently a source-family locator, not a guaranteed accepted literal evidence excerpt. A complete unpublished JSON Schema is not itself mandatory; ask only about actual representation/handoff uncertainties. No official score or certification is claimed.

The original public documentation release was Markdown-only. This local repair candidate contains the narrowly scoped changes documented in `REPAIR_PHASE_1.md` through `REPAIR_PHASE_5.md`, and is not live-benchmarked. New source-backed frozen metadata does not enable website content, hiring/activity or broad historical-name discovery. Browser/mobile rendering remains unverified; structural/JavaScript checks are not a visual score. Repository publication, emails and submission require separate authorization.

## Separate handoff materials

The five organizer drafts from the private development workspace are excluded, together with superseded command guides and audit history. Original local files remain untouched. This canonical guide, not older private handoffs, defines current commands. Historical source commits named above are provenance identifiers; their private Git history is not copied into this public repository.
