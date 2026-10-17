# Phase 5 — supported frozen facts without source requests

When the supplied authoritative CSV actually contains them, the normalizer
now emits registered business address, registered website metadata, entity
register registration date, incorporation date and latest-filed-year metadata.
Dates must parse exactly; blanks, malformed dates and empty address lists are
withheld. Postcodes remain strings. Source columns and the frozen digest are
retained. A separately checked current address/website takes precedence over
the same frozen metadata category; the frozen row remains inspectable.

A registered URL is not verified website content. A latest-filed-year flag is
not an annual statement or financial amount. Current fields are not evidence
of a historical change. No new source family or network request is introduced.

New targeted gate: 3 tests passed, including an actual zero-network CLI result,
missing/malformed metadata and narrow authenticated legacy-format migration.
The existing sanitized live-five suite still passes all 4 cases and exports
the original 166 claims. Source fixtures and genuine smoke reports are unchanged.

Financial comparisons remain deliberately guarded: the retained observations
do not supply a separately established unit/scale. No guessed unit is inserted
to manufacture a comparison. This secondary presentation limitation is deferred.
