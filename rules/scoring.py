"""Deterministic risk arithmetic (formula documented in docs/TECHNICAL_DECISIONS.md S3-02)."""

import math
from collections.abc import Iterable

SEVERITY_POINTS = {"low": 15, "medium": 35, "high": 60, "critical": 85}
AT_RISK = 60
WATCH = 30


def slip_points(days: float) -> int:
    """0 when on time; 45 for up to a day late, +10 per further day, capped at 85."""
    if days <= 0:
        return 0
    return min(85, 35 + 10 * math.ceil(days))


def combine(points: Iterable[int]) -> int:
    """Noisy-OR: independent reasons compound but never exceed 100; one reason scores itself."""
    remaining = 1.0
    for p in points:
        remaining *= 1 - min(max(p, 0), 100) / 100
    return round(100 * (1 - remaining))


def level(score: int) -> str:
    if score >= AT_RISK:
        return "at_risk"
    return "watch" if score >= WATCH else "ok"


HEALTH_LABELS = {"at_risk": "At risk", "watch": "Watch", "ok": "On track"}


def health(levels: Iterable[str]) -> str:
    """Roll-up for a project or tenant: the worst level among its active items."""
    seen = set(levels)
    for candidate in ("at_risk", "watch"):
        if candidate in seen:
            return HEALTH_LABELS[candidate]
    return HEALTH_LABELS["ok"]
