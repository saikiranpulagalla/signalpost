# Controlled P2 remediation — local candidate

Base: `16a7dea249abf4c72b30b619adefbdd80794a436`.
Branch: `codex/signalpost-p2-remediation`. No publication or official revision
is implied. Submitted V2 and previously published candidates remain rollback
points; no new live benchmark was performed.

## Accepted narrow fixes

|Fix|Change|Permanent regression|
|---|---|---|
|FIX-01|Existing integers display exactly and finite floats use their round-trip text; derived floating-point differences are explicitly approximate. Canonical claims, currency, period and scope unchanged.|[Financial presentation](tests/v2/test_p2_synthesis_exact.py)|
|FIX-02|One admitted membership set per module; same company/module ordering, plans, omission counts and budgets.|[Membership](tests/v2/test_p2_admission_membership.py)|
|FIX-03|Strict finite-number JSON decoding at source/JSONL boundaries and strict serialization at atomic JSONL/report/viewer publication. Non-finite fresh observations are fatal under both policies, not checked absence.|[Strict JSON](tests/v2/test_p2_strict_json.py)|
|FIX-04|Global zero request cap is passed literally into admission. Optional pairs are intentionally omitted before work; mandatory live identity still cannot be fabricated.|[Zero admission](tests/v2/test_p2_zero_admission.py)|
|FIX-05|Reserve generated/explicit observation-key paths before any artifact mutation, including resolved aliases and existing hardlinks.|[Key reservation](tests/v2/test_p2_key_reservation.py)|
|FIX-06|Role collections compare as canonical multisets only. Multiplicity and every role/date attribute matter; original evidence arrays/bytes are not reordered.|[Role permutations](tests/v2/test_p2_role_permutations.py)|

The strict codec rejects NaN/Infinity tokens and numeric overflow to a non-finite
float. It does not replace invalid values with null, invent absence or rewrite
historical bytes. Finite serialization is byte-compatible with the previous
stdlib options. Arbitrary-precision decimal arithmetic and JavaScript integers
above its safe range are not newly claimed. No dependency or source-family change.
The standalone refresh replay adds its script-derived root for the shared codec.

Zero-cap historical tests now assert zero admissions, no source evidence and
zero starts. The existing strict unattempted-source test retains its nonzero
exit/no-publication requirement using genuinely admitted work stopped before
transport start. No test method was deleted or skipped; these expectations
follow the explicitly authorized distinction between omission and non-attempt.

## Deferred, not silently repaired

**FIX-07 / BS-03 remains open.** The original field limit and truncated-sniffer
limitations reproduce. A tempting header-only dialect change breaks previously
accepted single-quoted fields for comma, semicolon and tab sources. A bounded
complete-record dialect design preserving accepted quoting variants was not
established in this cycle. No partial CSV parser patch or source truncation was
introduced. The private expected-failure reproducer is not a release regression
or a runtime dependency. Existing CSV/gzip behavior remains unchanged.

**DH-02 checkpoint policy remains unchanged: default25.** Local rich1500 fixtures
at25/250 preserved the same12135facts and valid completion; writes243/27 and
cumulative encoded bytes1,568,906,308/187,296,142. Peak process working sets were
approximately244MB in both cases. Wall samples95.96s/36.23s ran on a contended
host; they are not official performance guarantees. With paced synthetic source
responses, termination after30starts retained25/0optional pairs and authenticated
resume repeated5/30operations. With extremely fast fixtures, both could terminate
before an optional checkpoint and repeat30. No exactly-once cross-crash guarantee
or automatic interval change is made. A higher interval needs explicit operational
approval of the recovery/refetch trade-off, not a storage architecture rewrite.

## Compatibility evidence

Actual runner comparisons on identical synthetic100/1000/1100/1500inputs under
strict and best-effort retain respectively809/8090/8899/12135canonical facts,
with **zero canonical claim differences** and unchanged positive-budget plan
fingerprints. Fixture starts400/4000/4400/6000; external requests0. Ordered
seven-key JSONL, evidence links, report/profile/manifest hashes and viewer parity
are independently checked; Node JSON.parse accepts published artifacts.
These full-admission tests use explicit hypothetical20000s/7500request ceilings
and checkpoint intervals equal to batch size to isolate correctness, not
Builderr permissions or a change in production defaults.

Membership microbenchmarks separately isolate the algorithm: at1500dense
companies/five families, median baseline0.408s versus candidate0.00224s,
with identical mappings. Source latency and serialization are excluded.

Source allocation seeds/priorities, financial filing selection, temporal
presentation, network permission, evidence cutoff and the seven-key/six-state
contract are unchanged. BS-05/BS-06 and run-ID selection are out of scope.
All performance/source fixtures are synthetic and require no private captures.

## Verification procedure

Run the focused tests with a writable temporary directory, then the complete
V0/V1/V2 suites. Windows hardlink coverage requires filesystem support and may
need execution outside a restrictive sandbox. Do not skip the alias test to
claim verification. Exact-commit clean-checkout locked installation and artifact
checks are required before qualifying the local candidate. No source requests
or publication are part of these gates.

Final working-tree results: **V0 93/93, V1 104/104, V2 251/251 — 448 passed,
zero skips, exit0 for each suite**, including16new P2 tests. Initial V2 testing
overlapped CPU/I/O benchmarks and retained two obsolete zero-cap expectations
plus two short wall-deadline startup failures. The zero-cap tests were updated
as described above without dropping their security invariants. The unchanged
11-test transport suite passed alone, and complete V2 passed again without
parallel benchmarks or relaxed deadlines. Initial failures remain in private
verification logs; they are not silently counted as passes.
