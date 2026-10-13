# Network execution policy

Signalpost separates local validation from authorized research with a policy
that is enforced at the common HTTP boundary, not solely by CLI convention.

## `offline` (default)

`offline` is the default for `scripts/run_competition_batch.py`, tests,
fixture replay, saved-profile export, and contract checks.  The common
`norway_company_agent.http.fetch_json()` transport returns the explicit error
`network policy offline` before opening a connection.  The V1 official-source
transport (`v1_official.sources.live_fetch`) has the same default and does not
invoke its urllib helper unless the policy is authorized.

The competition runner rejects a non-zero-budget request for live enrichment
modules while offline before creating an in-progress report or transport.  An
explicit zero request budget is permitted to exercise truthful `not_fetched`
states; it cannot turn into an HTTP attempt.  It is not represented as
`not_available` or `ambiguous`.

## `authorized_public_brreg`

This policy requires both `--network-policy authorized_public_brreg` and
`--confirm-authorized-live-sources`.  It permits only HTTPS URLs on
`data.brreg.no`, with no credentials, fragment, or non-standard port.  The
competition runner disables redirects for its counted source calls.  Live
bootstrap additionally requires this policy.  Website and social publication
remain quarantined.

The caller must still scope the batch, worker count, timeout, attempts, and
`--max-source-requests`.  The physical-start counter is incremented before
each permitted fetch and prevents requests beyond the configured cap.  The
policy does not authorize protected endpoints or any non-BRREG host.

## Evidence

`tests/v2/test_network_execution_policy.py` exercises the real runner in
subprocesses with a guard at `urllib.request.urlopen`.  It verifies zero
physical HTTP starts for frozen identity-only and synthetic 100-company runs,
optional live-module refusal, zero-budget execution, saved-profile export and
resume, and unauthorized current-data refusal.  Results are recorded in
`OFFLINE_ZERO_NETWORK_TEST_RESULTS.md`.
