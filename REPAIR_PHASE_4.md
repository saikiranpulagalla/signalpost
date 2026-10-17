# Phase 4 — conditional, measured admission configuration

The hard source timeout remains 10 seconds in the proposed rich template.
Default forecasts remain conservative and unchanged unless a model is supplied.
`config/brreg-admission-observed-20261013.json` contains only aggregate saved
telemetry: 100 physical starts per family, source digest, tails and outcomes.
It contains no company rows, response bytes or private observation keys.

Forecast rule: observed maximum × 1.5 + 0.25 s, rounded up to 0.5 s. Missing
timings or failures first include the hard timeout before the margin. Values
are financials 4 s, roles 4.5 s, locations 4 s, registry-live 7 s, group 4.5 s.
The historical run had no transport failures/429s, so unseen tails remain
uncertain; 95 group 404s are measured checked outcomes, not transport failures.

Optional `--source-allocation-policy core_breadth` reserves matched financial
and role coverage before residual family waves. Default `family_waves` is
retained. Selection stays stable by run/module/organisation hash; execution is
still global family waves and output stays in original order. A suggested
bounded configuration uses `--source-family-budgets group=0`; this is intentional
omission, never proof of no corporate relationship.

Actual runner sensitivity gate: 20 combinations (100/1000/1100/1500 companies ×
hypothetical 5/10/20/45/90-minute budgets), using saved family maxima × 1.25
simulated source delays and two ideal workers. All 20 published every ordered
starter envelope, with zero unfinished admitted operations and zero external
requests. This is not a live benchmark. Three new tests passed in 149.375 s.

At hypothetical 90 minutes / 120-second reserve:

| Companies | Financials | Roles | Locations | Total fixture starts |
|---|---:|---:|---:|---:|
| 1000 | 1000 | 1000 | 514 | 2514 |
| 1100 | 1100 | 1100 | 302 | 2502 |
| 1500 | 1242 | 1242 | 0 | 2484 |

The old exact command admitted approximately 1054 starts: finance only at
1100/1500 and finance1000+roles54 at1000. Counts above reflect controlled
simulated clocks; real setup time may reduce admission. No private score
weights or numerical score gains are assumed.

An explicitly tested unexpected all-10-second-tail case FAILS CLOSED: its
conditional forecast is exceeded, scheduled work remains `not_fetched`, and no
false terminal success is published. Therefore this is **not a universal
runtime guarantee**. Unknown official budgets, source permission and cutoff
policy remain organizer-controlled. Use timeout-based forecasts or a smaller
explicit research scope if stronger worst-case conservatism is required.
