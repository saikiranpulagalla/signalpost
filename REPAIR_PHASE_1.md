# Phase 1 — bounded source I/O and rate limits

Base: `f69f587b5b16eed2ab195bf890d0d26c1f6262f5`. No live research.

The successful and HTTP-error body paths now use bounded chunk reads (2 MB
accepted-body default), a monotonic absolute deadline, and isolated daemon I/O.
The caller cannot be held by slow headers, DNS or a body. Cancellation shuts
down an exposed socket; if an opener cannot be interrupted, its private worker
cannot publish or block exit and its host is quarantined until it settles.
This does not claim Python/Windows is a hard-real-time operating system.

Batch reserve is passed to the transport. Decode/timeout failures retain
observed status, prefix-byte count and elapsed time; failed HTTP 200 bodies
cannot become available facts. Retry-After is recorded and a per-run host gate
prevents later waves starting during cooldown. Missing/invalid backoff stops
that service for this run. Already in-flight operations may finish.

Unattempted cooldown/quarantine work remains `not_fetched`. Its starter export
fails closed; stopping on a rate limit is not a fabricated successful batch.

Executed gates (exit 0):

- `python -S -m unittest discover -s tests/v2 -p test_transport_deadline_repairs.py -v`: 9 passed.
- `python -S -m unittest discover -s tests/v2 -p test_request_telemetry.py -v`: 3 passed.
- `python -S -m unittest discover -s tests/v2 -p test_network_execution_policy.py -v`: 5 passed.

Original audit functions replayed outside this repository, retaining the real
stdlib slow-body reader: previous slow-body run had 0 envelopes / exit 1;
repaired run produced 3 truthful terminal envelopes / exit 0. Standalone 20 ms
deadline returned a timeout in 32 ms (Windows scheduling tolerance), rather
than succeeding after 187 ms. The original 429 fixture now starts 2 requests,
not 6, and refuses publication because remaining admitted work was unattempted.
Raw audit inputs, private keys and disposable outputs are not release files.

Final accounting refinement: the shared physical counter is reserved inside a
bounded callback immediately before the opener. Transport policy, quarantine,
cooldown and cancellation refusals do not acquire a physical sequence number.
An actual-runner delayed-header test reconciles opener, ledger and telemetry
counts. The transport suite now has 11 cases; telemetry 3 and offline policy 5
also pass. An initial command used the nonexistent test_network_policy.py
pattern and ran zero tests; the correct five-case suite was subsequently run.

The final original slow-body replay exited 1 in 0.625 seconds with 5 starts and
no terminal file: one admitted operation was not reached in the extremely tight
300 ms budget. This supersedes the earlier replay's publication outcome, not
its evidence of bounded body reads. It is truthful fail-closed noncompletion,
not a promised successful batch. The 429 replay still stopped at two starts.
