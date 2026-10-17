"""Shared conservative family-wave admission; forecasts are not guarantees."""
from __future__ import annotations
import math


def latency_forecasts(value: dict, hard_timeout: float) -> dict[str, float]:
    """Read measured conditional forecasts, retaining the legacy flat format."""
    if value.get("schema") == "signalpost.admission-latencies/v1":
        value = {family: row["forecast_seconds"] for family, row in value["families"].items()}
    allowed = {"financials", "roles", "locations", "registry_live", "group"}
    if not isinstance(value, dict) or any(k not in allowed or isinstance(v, bool) or
            not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for k, v in value.items()):
        raise ValueError("Invalid conservative source latency model")
    return {family: float(value.get(family, hard_timeout)) for family in allowed}


def plan_family_admission(candidates: dict[str, list[str]], *, module_family: dict[str, str],
                          expected_seconds: dict[str, float], remaining_seconds: float | None,
                          request_slots: int | None, family_slots: dict[str, int], workers: int,
                          policy: str = "family_waves") -> tuple[dict[str, list[str]], dict[str, int]]:
    """Allocate first, then execute unchanged global family waves.

    core_breadth reserves matched financial/role company coverage before using
    residual resources. It assumes no private scoring weights. Remaining
    families retain explicit caller priority/caps, including optional group=0.
    """
    if policy not in {"family_waves", "core_breadth"}:
        raise ValueError("Unknown source allocation policy")
    selected = {module: [] for module in candidates}
    limits = dict(family_slots)
    remaining, slots = remaining_seconds, request_slots
    core = [module for module in ("financials", "roles") if module in candidates]
    if policy == "core_breadth" and len(core) == 2:
        maximum = min(len(candidates[m]) for m in core)
        for module in core:
            maximum = min(maximum, limits.get(module_family[module], maximum))
        if slots is not None:
            maximum = min(maximum, max(0, slots // 2))
        if remaining is not None:
            pair_wave_cost = sum(expected_seconds[module_family[m]] for m in core)
            maximum = min(maximum, max(0, math.floor(remaining / pair_wave_cost)) * workers)
        for module in core:
            family = module_family[module]
            selected[module] = candidates[module][:maximum]
            if family in limits:
                limits[family] -= maximum
            if remaining is not None:
                remaining -= math.ceil(maximum / workers) * expected_seconds[family]
            if slots is not None:
                slots -= maximum
    for module, options in candidates.items():
        family = module_family[module]
        admitted, cost = admit_wave(options[len(selected[module]):], workers=workers,
            expected_seconds=expected_seconds[family], remaining_seconds=remaining,
            request_slots=slots, family_slots=limits.get(family))
        selected[module].extend(admitted)
        if remaining is not None:
            remaining = max(0, remaining - cost)
        if slots is not None:
            slots -= len(admitted)
        if family in limits:
            limits[family] -= len(admitted)
    return selected, {module: len(options) - len(selected[module]) for module, options in candidates.items()}


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
