"""Shared conservative family-wave admission; forecasts are not guarantees."""
from __future__ import annotations
import math


def admit_wave(candidates: list[str], *, workers: int, expected_seconds: float,
               remaining_seconds: float | None, request_slots: int | None,
               family_slots: int | None) -> tuple[list[str], float]:
    limit = len(candidates)
    for slots in (request_slots, family_slots):
        if slots is not None:
            limit = min(limit, max(0, slots))
    if remaining_seconds is not None:
        limit = min(limit, max(0, math.floor(remaining_seconds / expected_seconds)) * workers)
    cost = math.ceil(limit / workers) * expected_seconds if limit else 0.0
    return candidates[:limit], cost
