# Phase 2 — authenticated history, live projections and last-known-good evidence

History is no longer authenticated by checking only current claims. Explicit
refresh independently verifies the prior frozen baseline and optional sources,
then signs a separate private-key history bundle. Resume, starter export and
viewer generation require that anchor. Events are reconstructed from the
paired records, including stable ID, values, attribution, dates, digests and
classification. Duplicate events and future nested retrieval times are refused.
Keys are not part of output/publication. Old history lacking an independent
anchor is rejected, not silently trusted or re-signed.

Live identity uses one projection function shared by construction and reuse.
All supplied scoreable fields and derived registry/accounting records are
checked against the authenticated live record. Legitimate missing legacy
projection fields can be rederived; unsupported additions cannot be retained.

Authenticated prior available observations survive current failures in
`historical_observations` and `previous_evidence` in profiles. They are visibly
labelled historical in the viewer, never inserted as current starter claims.
Material-change creation is independent of last-known-good retention.

Executed gates (no external source requests):

- New retained-history suite: 5 tests passed, including nine independent
  mutations tested through actual resume/export, eleven live projection
  mutations, signed-but-inconsistent events, valid export/viewer/reuse and
  503/timeout/403 refresh cases.
- Adjacent `test_independent_audit_repairs.py`: 14 passed, including original
  seven isolated current-fact mutations, financial reuse, transaction locks,
  explicit unchanged/changed refresh and actual large-batch publication.
- Original F03 exploit now stops at invalid completion after resume rejection;
  original F04 stops at live-baseline verification; both exit 1 instead of
  demonstrating publication. New tests separately verify exporter rejection.
- Original F05 replay exits 0 and now finds the old financial amount/digest in
  new profiles/viewer while current financial claims stay empty. Old inputs
  remain unchanged. Audit output and authentication material are external.

Rebuilding a standalone viewer of authenticated historical profiles requires
`--observation-key` pointing to the private local key. Do not publish the key.
