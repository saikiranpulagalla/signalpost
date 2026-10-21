# BS-01: physical-LF JSONL framing

This narrow repair descends from published Revision 1 candidate
`62ae88172a1d99811070229def9de6dfcef1c2a3`. Submitted V2/main and that published
commit remain historical rollback points; this document does not announce a
push, official revision or new live benchmark.

## Invariant and implementation

Only physical LF (byte 0x0A) frames a JSONL record. Literal U+2028, U+2029 and
U+0085 inside a JSON string are data, not record boundaries. The previous
`str.splitlines()` readers fragmented valid UTF-8 JSON produced by the unchanged
`ensure_ascii=False` writer. Original exact-commit replay exited 1 with two
independently valid terminal rows and an incomplete manifest.

`v0_kernel.storage` now supplies the authoritative `iter_jsonl`, `read_jsonl`
and `read_jsonl_bytes` readers. Path reads stream binary LF records; already
pinned bytes use the same parser without reopening or rewriting the source.
Strict UTF-8 decoding preserves all string contents. LF and CRLF, a complete
last record without newline, and existing space/tab blank records are accepted.
CR is removed only as LF's CRLF partner; CR-only termination is rejected.
Malformed JSON, invalid UTF-8, truncated records and multiline JSON fragments
are rejected. Callers requiring objects may explicitly request object validation.
The existing V0 UTF-8-BOM allowance remains explicit and first-record-only.

The atomic writer, source bytes, SHA-256 calculations, evidence authentication,
JSON values and dependency versions are unchanged. A differently serialized
but semantically equivalent input can legitimately have a different digest.
No existing historical artifact is rewritten or re-attested.

## Consumer inventory

Updated production/lifecycle consumers:

- `v0_kernel/storage.py`: shared streaming/path/pinned-byte readers.
- `v0_kernel/contracts.py`: JSONL branch of V0 batch parsing only.
- `v1_official/refresh.py`: already-hashed prior output.
- `src/norway_company_agent/batch.py`: JSONL organisation input only.
- `src/norway_company_agent/completion.py`: persisted envelopes/profiles.
- `src/norway_company_agent/scrapy_crawler.py`: existing profile reader only;
  quarantine/source behavior is not changed or enabled.
- `scripts/run_competition_batch.py`: checkpoints/resume, previous profiles,
  final committed envelope read; uses already-read bytes for final validation.
- `scripts/build_profile_viewer.py` and `scripts/export_starter_compatible.py`:
  standalone profile/envelope loading. Object-only export validation remains.

The following supporting readers now delegate to the same parser (imports and
framing only; no acquisition or scoring logic changes):

`apply_verified_site_seeds.py`, `benchmark_registry_declarations.py`,
`build_source_latency_model.py`, `evaluate_coverage.py`,
`extract_company_site_activity.py`, `extract_company_site_news.py`,
`generate_submission_smoke_report.py`, `normalize_social_links.py`,
`release_gate_v0.py`, `release_gate_v1.py`, `run_brave_discovery.py`,
`run_scrapy_websites.py`, `sanitize_live_baseline.py`,
`simulate_evaluator_budget.py`, `v2a3_scale_smoke.py`,
`verify_source_snapshots.py` (all under `scripts/`). Existing standalone script
root-path setup is extended where needed to find the checked-in shared helper.

`scripts/run_live_smoke.py` also adds its script-derived repository root before
importing `batch.py`, which now imports the shared reader. The original full
gate exposed this missing bootstrap path; the unchanged authorization-refusal
test reproduces the failure and passes after the one-line correction. Guarded
startup checks from the checkout and an unrelated directory confirm refusal
before source access. The other source-only bootstrap, `run_refresh_replay.py`,
does not import the shared reader through its current dependency chain and
passes direct startup checks without modification.

Intentionally unchanged:

- `batch.py` and `v0_kernel/contracts.py` plain-text ID-list branches process
  general human-readable text, not JSONL.
- `website.py` and `v2_web/robots.py` split robots text, not JSONL.
- `select_coverage_sample.py` and `v1d_prepare_live_smoke.py` already iterate
  gzip streams in binary mode, so record framing is physical LF. Their bounded
  record checks, pinned hashes and source formats remain untouched.
- Single-document JSON, CSV, ordinary text, URLs and source field normalization
  are not JSONL and were not mechanically changed.

## Test and independent-oracle coverage

`tests/v2/test_jsonl_framing.py`: separate/combined separators, positions,
Norwegian text, quotes/backslashes, literal/escaped JSON representations,
LF/CRLF, final-newline variants, blank records, atomic writer preservation,
streaming behavior, explicit BOM policy, malformed/truncated/invalid-UTF-8 and
CR-only negatives, and object-only parsing.

`tests/v2/test_jsonl_lifecycle.py`: real guarded competition-runner subprocesses
under strict and best-effort. Fields include legal names, declarations, former
names, annotations, role names and source notes. It exercises intermediate
checkpoints, completed profiles, primary seven-key output, zero-new-source resume,
explicit unchanged/material refresh, preserved old/new evidence, saved internal
export, and standalone viewer generation from a different working directory.

The independent oracle splits original artifact bytes on LF, imports no shared
reader, checks ordered membership, values, public availability, evidence links,
profile/primary consistency, report/profile/envelope hashes and viewer payloads.
Application completion verification is also run but is not the sole oracle.
The two new modules contain 11 passing test methods with parameterized cases.
No existing tests or fixtures were removed, weakened or skipped.

The original 1,000/1,100/1,500-company inputs were replayed with controls under
both policies: all 12 executions exited 0 with exact terminal counts, preserved
raw registry values, valid completed bindings and zero source requests.
Local ceilings of 300 seconds and a 120-second reserve are hypothetical tests,
not official permissions.

## Performance bounds and interpretation

One traced 1,500-company rich synthetic comparison used identical fixtures,
two workers, a checkpoint interval of 1,500, 7,500 maximum fixture requests and
a hypothetical 20,000-second virtual-source ceiling to admit every selected
wave. Those are benchmark configurations, not official limits or defaults.

|Measurement|Original ASCII|Repaired ASCII|Repaired Unicode|
|---|---:|---:|---:|
|Ordered terminals|1,500|1,500|1,500|
|Published facts|12,135|12,135|12,135|
|Simulated transport calls|6,000|6,000|6,000|
|External network starts|0|0|0|
|Peak traced Python allocations, bytes|220,188,184|218,922,713|218,922,013|
|Actual local wall seconds|38.890|32.453|36.281|
|Cumulative JSONL write seconds|8.050|6.596|7.062|
|Completion-check seconds|1.944|1.940|1.877|
|Profile/envelope/report/viewer bytes|46,993,562|46,993,562|46,993,373|

ASCII canonical fact sets were exactly equal before/after. Unicode text differs
intentionally only in the altered fixture source fields. These are single
instrumented samples with local contention and tracemalloc overhead, not total
RSS, a statistically established speedup or a live-source runtime benchmark.
Path reading no longer retains a decoded whole-file plus split string list;
parsed result lists are still retained by the existing callers.

## Preserved scope

BS-02 through BS-06 are not repaired here. Financial selection, registry temporal
rules, role ordering, scheduling, budgets, network permissions, 429 handling,
locking, source transport and seven-key semantics are unchanged. Fresh raw-byte
validation and authenticated history remain mandatory. Invalid prior receipts
must be revalidated/refetched rather than silently trusted. Private keys and
real source captures do not belong in this source commit.

Only a separately authorized publication may move a public branch. This local
repair does not submit an official revision or establish an official score.

## Final working-tree gate

After the one-line standalone-bootstrap correction, the unchanged historical
authorization-refusal test passes. The final complete suites pass: V0 93/93,
V1 104/104, V2 235/235; 432 total, exit 0, no skips. This includes the 11 new
framing/lifecycle tests. All 12 original-size/control CLI cases also pass the
independent LF oracle. Invalid evidence, claims, attribution, reporting periods,
completion hashes and membership remain rejected; invalid receipts are not
reused. The local descendant still requires exact-commit clean-checkout
verification and separate authorization before any publication.
