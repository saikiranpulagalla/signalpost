# Source integrity is independent of completion policy

## Confirmed discrepancy

The same synthetic source corruption was reproduced against submitted V2
`cf6857371497a8a1b565715d1daacf7602e0749c` and V3
`00ede95b3e7d9e14189226f8bdf4746f94ef8f52`. The transport seam retained valid
221-byte source JSON and a matching parsed financial observation, but replaced
its source digest with 64 zeroes. Both strict runs published the available
`financial_annual_result` claim citing that digest, signed the normalized
observation and completed successfully. V2 emitted 579 fixture facts; V3 809.
The differing totals reflect existing projections, not the injected mutation.

This is a **P1 evidence-validation defect**. It is not proof of a wrong amount,
a BRREG incident, an externally exploitable hash substitution, or corruption
of the genuine historical smoke. The production HTTP transport independently
computes hashes from bytes; the defect was missing defense at the boundary
between a successful fresh transport result and trusted signed evidence.

The best-effort implementation had added the required fresh-result check but
incorrectly restricted it to that completion policy. Strict mode trusted the
transport's declared hash. Metadata format validation, local HMAC receipts,
claim reconstruction and completion hashes could all be internally consistent
with the false hash. They do not independently prove byte integrity or BRREG
origin. A local receipt must be issued only after validating its fresh input.

## Minimal repair

The existing check in `scripts/run_competition_batch.py:bounded_fetch` now
applies to every successful HTTP-200 result, before normalization, receipt
creation or optional snapshot retention. It requires a complete original body,
matching byte count, matching SHA-256 and a parsed body equal to those bytes.
Both strict and best-effort reject inconsistent fresh input as an integrity
failure, not as a recoverable transport failure. Source admission, network
authorization, concurrency, requests, deadlines and availability mappings are
unchanged.

This does not require previously persisted historical observations to invent
missing source bytes. Existing independent legacy pins, captured-source checks,
observation receipts and authenticated refresh paths are unchanged. A legacy
receipt remains evidence of the retained observation, not retroactive proof
that unretained raw responses were authenticated. Previously signed bad input
cannot be distinguished solely from its internally consistent receipt; this
repair is not a claim of retroactive historical attestation.

The old synthetic bootstrap test supplied placeholder hashes and no raw bytes.
It now provides its unchanged source facts as JSON bytes with real size/hash;
all original assertions remain. New tests deliberately reject missing bytes
and false hashes, so this fixture correction does not grandfather invalid fresh
results. No real historical fixture or source record was modified.

## Verification and publication semantics

`tests/v2/test_source_digest_consistency.py` includes the formerly failing
strict test, independently mutated digest/body/count/missing-bytes/incomplete/
invalid-JSON cases under both policies, and nominal fact equivalence. The
actual CLI uses deterministic source fixtures with external sockets forbidden.
Successful runs require ordered starter output, current viewer/profile parity
and full completion bindings. Digest rejection never publishes the unsupported
financial claim or a completed manifest.

Strict mode can leave diagnostic/partial artifact files after a fatal worker
error; these are not completed output. Always require exit zero AND a verified
completed manifest. Best-effort rejects fatal integrity errors before terminal
publication. An explicit wrong-company financial record is withheld by the
existing strict normalizer; best-effort additionally treats that rejection as
a fatal batch error. Neither policy publishes the mismatched claim. These
existing identity/completion semantics are not broadened by the repair.

## Official policy recommendation

Prefer explicit `--optional-research-policy best_effort` for an authorized
revision: preserve supported facts when optional requests fail, without
converting unattempted work to checked absence. Keep exact input, mandatory
identity and evidence failures fatal. Strict mode remains available, with the
same fresh-byte validation, but is not a mechanism for requesting a rerun.

| Condition | Expected treatment and scoring uncertainty |
|---|---|
|Individual timeout/failure|Attempted failure; retain other valid facts. This need not void the batch.|
|Entrant-specific throttling|429 plus cooldown; stop new affected-service starts. Not automatically a shared outage.|
|Shared BRREG outage|Retain error/status/timing/Retry-After evidence. Official rules say shared-source failures are void/rerun, but recognition of exit-zero degraded runs is unspecified.|
|Regional network failure|Preserve transport diagnostics; ownership is organizer-determined, not automatically a shared-source failure.|
|Request/time budget stop|Preplanned omissions stay unscheduled; admitted unstarted work stays not_fetched internally with explicit diagnostics. No checked absence is invented.|
|Ordinary checked source absence|Use the supported checked-source outcome; endpoint 404 is not proof of absence everywhere.|
|Integrity/mandatory identity failure|Withhold invalid claims; never fabricate completion or use graceful source recovery to bypass validation.|

A degraded successful batch might be scored on sparse evidence unless Builderr
identifies a shared outage. A nonzero exit does not itself establish eligibility
for a void/rerun and can instead become an entrant-caused zero. Do not force
failure to manipulate that decision. Supply the terminal errors, report,
telemetry and completion marker for diagnosis; private authentication keys
must not be sent. The remaining policy question is whether shared failures
are recognized independently of agent exit status and optional completion.

Final local regressions after the runtime repair: **V0 93, V1 104, V2 224;
421 passed, no failures or skips**, exit zero for each suite. The three new
test methods include twelve independent negative policy/mutation combinations.
Nominal rich rehearsal retained exactly the same 809 distinct canonical facts
under both policies (zero additions or losses). Fourteen actual-runner cases
covered nominal, 429, ten-second simulated sources, transport exception,
timeout, identity conflict and corrupt digest under each policy. All external
source starts were prohibited. Historical smoke profile/envelope/telemetry
SHA-256 pins remained unchanged. Timing is local processing or simulated source
latency, never a new live benchmark.

Submitted main is unchanged. No source requests, publication, email or official
revision are part of this local repair. Prepare a verified revision rather than
wait indefinitely on organizer feedback about an independently demonstrated
integrity defect; actual publication still requires user authorization.
