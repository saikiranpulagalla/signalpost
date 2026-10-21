# Bounded optional research completion — local candidate

Submitted V2 `cf6857371497a8a1b565715d1daacf7602e0749c` remains unchanged.
This narrowly scoped change builds on local V3
`4e5d0e82d7d76357e4f766a3c87bf793da0500e5`. No network request, push, email,
deployment or official revision was performed to verify it.

## Contract and explicit policy

The retained Builderr evaluation contract requires one terminal envelope per
input and recognizes source-level failure/blocked states. The starter envelope
has separate `run`, `claims`, `evidence`, `errors` and `operations` members.
It does not require every optional family to supply a positive fact and does
not define an availability meaning for research that was never attempted.

The new opt-in CLI argument is:

```text
--optional-research-policy best_effort
```

The default remains `strict`. Existing installation, batch/frozen-registry paths,
source modules, core_breadth/latency configuration, resource caps and explicit
network authorization are unchanged. This flag does not authorize networking,
enable an automatic fallback, reduce timeouts or invent an official budget.
It must be selected before execution; its policy is bound into the immutable
execution-plan hash and resume fingerprint.

For a verified frozen baseline, optional research is explicitly best-effort:

* Successful observations and their original evidence remain published.
* Attempted 403/404/429, timeout and transport failures retain their honest source
  outcomes. A 429 still activates the existing service-wide cooldown; no retry
  is introduced.
* Admitted work refused by the request/deadline/cooldown/quarantine guard remains
  `not_fetched` in internal profiles and report counts. Its public `errors`
  entry says it was selected but **not attempted**, with the actual local reason
  and decision time. No available fact, source snapshot or checked absence is
  fabricated for it. No seventh public availability value is emitted.
* Optional work remains selected in the original plan; unfinished admission is
  never retrospectively renamed intentional omission.
* A completed terminal workflow can therefore contain source failures or
  explicit unattempted-work diagnostics without pretending research succeeded.

This is starter-compatible behavior, not a claim of official evaluator
certification or a score prediction. An evaluator requiring all optional
research to succeed would need to select strict mode; the retained documents
do not impose that requirement.

## Nonrecoverable failures and ownership

Missing official frozen identities, wrong source/parent attribution, corrupted
source bytes/digests/projections, invalid metadata, fabricated persisted claims,
normalization/validation exceptions and arbitrary worker failures stay fatal.
Successful source results in best-effort mode must have matching original bytes,
digest, byte count and decoded body. Baseline and claim integrity are checked
before adaptation. A fatal worker failure prevents new public JSONL publication
and completed-manifest creation in best-effort mode.

Only an exception at the isolated transport-call boundary **after an accounted
request start**, returning no source observation, can become an ordinary failed
source. Parser/type/validation exceptions are not downgraded. Live bootstrap
still requires every exact legal identity; its transport exceptions cannot be
used to manufacture a baseline.

Workers in best-effort mode receive detached profile copies. Every executor
context joins before final validation/publication. This implementation does not
pretend `Future.cancel()` kills a thread. The existing absolute-deadline HTTP
transport isolates late I/O and quarantines its service; its private progress
cannot mutate published profiles. A worker stuck outside that bounded transport
is not claimed recoverable. An externally terminated process never records
success; supported explicit resume reuses only authenticated checkpointed work.

Whole-run OS ownership, atomic writes and report/output/profile/input/plan hash
bindings remain in force. Exit 0 requires valid ordered terminal results, no
fatal worker failure, successful completion verification and publication before
the overall deadline. Exhausting the finalization reserve, filesystem failure,
lost ownership or invalid bindings still prevents success.

## Actual CLI evidence — synthetic, zero external requests

The pinned pre-change runner was compared with the candidate on identical
synthetic batches, two workers, one attempt, ten-second request ceilings,
120-second finalization reserve and the unchanged measured-family model.
100-company cases used a hypothetical five-minute budget; larger cases used
hypothetical 90 minutes. These are not official allowances or live benchmarks.

| Companies | Scenario | Baseline completed | Best-effort completed | Simulated starts | Published candidate facts |
|---:|---|---:|---:|---:|---:|
|100|Sustained ten-second tails|No|Yes|36|336|
|100|429 cooldown|No|Yes|1|300|
|100|Accounted transport worker exception|No|Yes|84|383|
|1,000|Sustained tails|No|Yes|1,056|4,056|
|1,000|429 cooldown|No|Yes|1|3,000|
|1,000|Transport exception|No|Yes|2,000|4,999|
|1,100|Sustained tails|No|Yes|1,056|4,356|
|1,100|429 cooldown|No|Yes|1|3,300|
|1,100|Transport exception|No|Yes|2,200|5,499|
|1,500|Sustained tails|No|Yes|1,056|5,556|
|1,500|429 cooldown|No|Yes|1|4,500|
|1,500|Transport exception|No|Yes|2,484|6,983|

All successful cases had exact ordered membership, starter validation, preserved
supported facts, request reconciliation, viewer/profile parity and verified
completed manifests. Strict source-error cases sometimes retained valid JSONL,
but their nonzero exit and in-progress marker are correctly counted as failures.

Additional paired cases cover nominal execution, source denial/timeout, identity
conflicts, byte/hash/projection corruption, validation exceptions and true
normalizer-worker failure: 42 CLI executions in total. Negative integrity cases
never complete under best-effort. These source-shaped fixtures are not real
company observations and do not imply factual coverage or official points.

Eight focused tests additionally exercise process termination (exit 17), recovery
from authenticated checkpoints, unchanged resume with zero new starts, independent
forged-claim refusal, strict behavior and no fabricated public availability.
Final full regression: **V0 93, V1 104, V2 221 — 418 passed, no failures/skips**.

One paired 100-company nominal `tracemalloc` sample measured peak Python
allocations of 14,748,629 bytes before and 15,032,470 after (+283,841, about 1.9%).
These are not total RSS or official-machine memory figures. Single-run local
wall times were 2.766s and 2.250s; no speedup is inferred. Larger-case measured
wall times include serialization/checkpoints and concurrent local test load.

## Limits and release interpretation

Best-effort reduces the demonstrated avoidable failure modes without reducing
factual or publication integrity. It does not guarantee an arbitrary batch fits
unknown evaluator resources, solve mandatory identity failure, recover a killed
process without retry, or make unattempted research satisfy recall. Request
accounting is per execution; explicit recovery can repeat an uncheckpointed
in-flight operation after process death. No exactly-once cross-crash guarantee
is claimed. Existing telemetry isolation requirements still apply on retry.

The exact submitted V2 remains active. A local verification GO for this opt-in
behavior is not authorization to push or revise the submission. Official
resource/network/cutoff and artifact-handling settings remain organizer-owned.
