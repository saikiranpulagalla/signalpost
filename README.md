# Signalpost

Evidence-backed company research for Norwegian organisation numbers: verified registry facts, legal-entity financials, registered roles, parent-linked subunits, explicit relationships, authenticated refresh, source-linked synthesis and a standalone viewer.

## Requirements and installation

Python **3.12** and [uv](https://docs.astral.sh/uv/). From this repository's root:

```powershell
uv sync --locked
```

## Working offline sample

```powershell
uv run --no-sync python -S scripts/run_competition_batch.py --organisations tests/v0/demo/organisations.jsonl --bulk tests/v0/demo/frozen-registry.csv --modules registry,accounting_obligation --network-policy offline --output-target starter_compatible --profiles-output out/sample/profiles.jsonl --output out/sample/envelopes.jsonl --report out/sample/report.json --viewer-output out/sample/viewer.html --run-id sample
```

Use a fresh output directory. Results: `out/sample/envelopes.jsonl`, full profiles, a report/completion manifest and `out/sample/viewer.html` (open locally). The sample is synthetic and starts no company-source HTTP requests.

See the **[canonical submission guide](SUBMISSION_READY_README.md)** for conditional rich research, evidence, refresh and organizer-dependent configuration.

## Evidence and boundaries

The [earlier genuine 100-company smoke](release_artifacts/submission/HUNDRED_COMPANY_GENUINE_SMOKE_REPORT.md) recorded 100 identities, 3,046 claims, 605 evidence metadata records, 500 requests and 264.112 seconds. It used an earlier source commit, not this publication; metadata does not imply every raw response was retained.

Default network policy is offline. Public BRREG research requires explicit authorization and both the authorized network policy and confirmation flag. No website/social crawling is enabled. Source terms, privacy and resource limits apply; see [source rights and costs](SOURCE_RIGHTS_AND_COST.md). Starter-compatible output is not official evaluator certification; cutoff and artifact-handling details remain organizer-controlled.

[Publication provenance](PUBLICATION_PROVENANCE.md) identifies the verified source release and intentionally excluded local history. This public repository has independent clean history; no private live outputs or original Git history are included.
