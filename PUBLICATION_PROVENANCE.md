# Public snapshot provenance

This repository starts a new clean Git history. It does not contain the private development repository's commits.

- Exact selected source: `10c9ccabd3915be1b7d758b57e9500c3a4def1c7`.
- Previously tested runtime: `8ffe82d01ff5c2182d32e187e9444f680e3457ed`.
- Approved source ZIP SHA-256: `0a90e4ca55dd2428d87866d5d2187df2e73c396a471fca94fca76e1ca8e309c5`.
- Genuine earlier smoke source/report commit: `553e95835857396b282345e2e72d9f1d9e1ceaca`, not the newly published commit.

Production Python, permanent V0/V1/V2 tests, sanitized fixtures and locked dependencies are copied byte-for-byte from the selected source commit. Intentional changes are publication documentation only: a minimal README, canonical guide publication/provenance wording, this provenance file and the explicit file manifest. No application features, source families or test assertions change.

## Selected and excluded content

Included: research/validation/scheduling/refresh/source-policy modules; production and required verification entry points; all three advertised regression suites and sanitized fixtures; frozen demo; dependency files; canonical guide; source-rights disclosures; genuine smoke report and aggregate metrics. Disabled legacy entry points remain only where regression tests verify their quarantine.

Excluded: original `.git`, private live profiles/telemetry/captures/authentication keys, complete company universe, caches/environments, archives, stale command guides, audit/release history, development logs and organizer draft emails. All excluded originals remain in the untouched private workspace.

`data/universe-metadata.json` contains reference digests and aggregate counts, not the original universe or historical frozen registry. Normal execution accepts a caller-supplied batch and matching frozen registry. Historical selection helpers still require their exact external data; they do not download or substitute it automatically.

## Evidence limitations

The genuine smoke report and aggregate metrics are retained unchanged. They summarize a prior authorized run; the public repository is not an independent replay of all 605 evidence records against original response bytes. Private live artifacts and their private Git history are intentionally absent. The five-company fixture is a sanitized normalized representation, not a raw-response archive; it retains original evidence digests as provenance, not hashes of the sanitized fixture.

Registered role holders in public-source research require privacy minimization. Distributed fixture role-holder names are sanitized. No protected identifiers or credentials are intended for distribution. Source/API rights disclosures apply independently of this project's code; this publication does not invent a software licence or replace upstream terms.

## Reproduction

Use Python 3.12, `uv sync --locked`, then the README offline sample. Some unchanged regression fixtures expect the ignored writable `.release-gate-tmp` directory to exist before test discovery. Create it explicitly (portable Python command), then run the complete advertised suites separately:

```powershell
python -S -c "from pathlib import Path; Path('.release-gate-tmp').mkdir(exist_ok=True)"
python -S -m unittest discover -s tests/v0 -q
python -S -m unittest discover -s tests/v1 -q
python -S -m unittest discover -s tests/v2 -q
```

On Windows, use a writable local temporary directory if default TEMP permissions block atomic replacement. Verification results and the exact newly published commit are supplied in the release handoff; do not infer new test results from old reports.
