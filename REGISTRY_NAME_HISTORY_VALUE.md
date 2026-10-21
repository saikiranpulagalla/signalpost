# Registry response value — local candidate, not submitted

## Evidence and scope

Submitted V2 remains `cf6857371497a8a1b565715d1daacf7602e0749c`.
This narrow change builds on local V3 `28e338ac3ed33c8fa32bd0f875072293bcf5fe67`.
No new source requests, source families, scheduler defaults, latency forecasts,
dependencies, output-envelope keys or historical smoke artifacts were changed.

The existing authorized pilot has 20 exact public BRREG entity responses, pinned
by original byte hashes and a private acquisition-boundary receipt. The index
SHA256 is `3385df94d9c88cca9ef180ee5583ffb515f813d012641c87c548105a446652ed`.
Local authentication is not a cryptographic signature from BRREG. Raw bodies,
private keys and disposable study outputs are deliberately not distributed.

On these same retained current observations:

| Published coverage | V2 | Previous V3 | This candidate |
|---|---:|---:|---:|
| Companies with facts / ordered envelopes |20|20|20|
| Registered activity companies |0|20|20|
| Registered purpose companies |0|20|20|
| Companies with supported former-name intervals |0|0|6|
| Canonical company/field/value facts |168|208|214|

Six history claims contain ten actual former-name intervals. Each company's
history is one structured claim, not multiple current-name aliases. Six companies
also have identical activity/purpose text: 40 declaration fields are only 34
distinct company/text values, not 40 distinct business descriptions. The added
history family requires zero additional requests when registry-live is already
fetched. It does not make currently omitted registry requests free.

## Projection and trust boundary

`historiskeNavn` is projected only with exact parent entity identity, authentic
source bytes and a known retrieval time. Preserve original `fraDato`/`tilDato`
strings, require valid ordered intervals, deduplicate/sort records, and withhold
missing, future or timezone-ambiguous current-day endpoints. The source wall-clock
timestamps have no supplied timezone; none is invented. Historical names are never
promoted to current names or manufactured change events.

The canonical field is `registered_name_history`, backed by `/historiskeNavn`,
existing source URL/digest/retrieval evidence and the known
`signalpost-registry-history/2026-10-20` extraction method. Full profiles retain
the locator; starter evidence uses the existing source-family span. Cited
synthesis labels former names and source intervals. The standalone viewer reads
these profiles; no separate frontend or new envelope key is required.

Versioned normalization preserves authenticated older observations without
retroactively inventing discarded fields. Identical resume makes no new source
calls. Explicit authenticated refresh compares sorted historical-record sets;
reordering or an unchanged observation creates no material event. A documented
revision of the source history can create an evidence-backed history-record
change, not an independently witnessed renaming. Missing current history is
unknown, not a fabricated deletion or checked negative.

## Fields deliberately not added

The same bodies contain email in 6/20, telephone in 4/20 and mobile in 5/20.
Corporate versus personal ownership cannot be established from these records;
no contacts are projected. Registered website metadata exists in 3/20 and is
already supported; it proves no website content. Business addresses, industry,
legal form and explicit status values are already supported. Postal addresses
occur in 6/20; additional address detail adds no demonstrated new family here.

Entity-register and establishment dates exist in 20/20; business-register dates
in 19/20. Their semantics differ. Frozen-CSV projections already support two
of these fields when genuinely supplied; this one-family change does not add
aliases or speculate about scoring credit. Nominal capital (17/20), institutional
classification (20/20), articles dates (19/20), register-membership booleans and
employee-availability indicators are not new financial performance, operations,
relationships or independently observed changes. They remain deferred rather
than inflated into extra categories. Absent employee counts are not zero.

## Allocation experiment — not an official benchmark

48 actual-CLI synthetic scenarios tested 100/1,000/1,100/1,500 companies, three
hypothetical budgets (5/45/90 minutes), and four policies. All published complete
ordered starter-compatible batches with completion/viewer bindings. Delays were
virtual two-worker replays of saved genuine family timings; company facts were
synthetic. Positivity frequencies are illustrative, not nationwide estimates.
The original model remains 4s financials, 4.5s roles, 4s locations and 7s registry.
No 0.906s pilot-based forecast was substituted.

At a hypothetical 1,000-company/90-minute configuration, all policies preserve
1,000 financial and 1,000 role operations:

| Policy | Location operations | Registry operations | Total operations |
|---|---:|---:|---:|
| Current core |514|0|2,514|
| Registry-first residual |0|294|2,294|
| Mixed, registry cap 100 |340|100|2,440|
| Balanced residual, registry cap 170 |216|170|2,386|

The mixed policy retains some location coverage, but is not universally superior.
All four are Pareto trade-offs at this resource boundary. At 1,100 companies the
mixed cap is 110; at 1,500 the current conservative core consumes the budget
before either locations or registry. Rich extraction alone still gives no gain
where registry-live is not admitted.

The existing CLI can express the mixed experiment without a new scheduler:

```text
--source-allocation-policy core_breadth
--source-priority financials,roles,registry_live,locations,group
--source-family-budgets group=0,registry_live=100
--source-latency-model config/brreg-admission-observed-20261013.json
```

These are configuration fragments for the stated experiment, not a complete
submitted command, official budget or recommendation for every batch size.
The existing explicit network authorization remains mandatory. Financial and
role breadth is reserved first. Organizer-provided batch/snapshot paths and
permitted resource/cutoff conditions must still be bound normally.

## Failure tails and release decision

Saved timings, 1.5x slowdowns, checked 404s, blocked registry access, one timeout,
larger output and a 2,200-request cap were exercised through the actual runner.
Sustained ten-second delays and service cooldown can leave admitted work
unfinished and refuse successful completion. A worker exception can retain
locally valid envelopes while the process exits nonzero and the completion
marker remains in progress. These are unsuccessful runs, not fabricated passes.
Publication risk under unseen tails is not eliminated by any allocation here.

Eight focused tests cover source validity, missing/invalid/future intervals,
Unicode/hostile text, seven independent forgeries, citations/viewer parity,
legacy evidence, zero-call resume and authenticated refresh. Complete regression:
V0 93, V1 104, V2 213 — **410 passed, no failures or skips**. An initial Windows
sandbox temp-directory failure was resolved by using a controlled writable temp
path, without modifying tests or production storage behavior.

**PARTIAL: retain submitted V2.** A real new historical-name family is proved on
the pilot, but hidden cross-family weights, actual resource limits, current-source
cutoff compatibility and collection of synthesis/viewer sidecars are unresolved.
The official 70% company / 30% fact weighting is within a type, not permission to
invent cross-type weights or reference denominators. No new official score was
available in retained local materials; no external feedback request was made.
