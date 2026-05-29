"""Per-pool readiness scoring (0..100) and "five biggest blockers" rollup.

The score is a deliberately blunt instrument intended for executive-summary slides.
The detail still lives in the recommendations list — this module only summarizes them.

Score model (deductive, starts at 100):

* Each ``blocker`` recommendation:   −10 (capped at −60)
* Each ``warning`` recommendation:   −2  (capped at −30)
* Each ``info`` recommendation:      0   (no impact, advisory only)

We never go below 0 or above 100. Buckets:
* 80 .. 100 → "ready"
* 50 ..  79 → "ready-with-effort"
*  0 ..  49 → "blocked"

Blockers are picked by (severity desc, effort desc) — i.e. high-severity / high-effort
items rise to the top of the "five biggest" list.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .models import Recommendation

Bucket = Literal["ready", "ready-with-effort", "blocked"]

_SEVERITY_WEIGHT: dict[str, tuple[int, int]] = {
    # severity -> (per-item deduction, max-deduction)
    "blocker": (10, 60),
    "warning": (2, 30),
    "info":    (0, 0),
}
_EFFORT_RANK: dict[str, int] = {"high": 3, "medium": 2, "low": 1}
_SEVERITY_RANK: dict[str, int] = {"blocker": 3, "warning": 2, "info": 1}


@dataclass(frozen=True)
class ReadinessScore:
    score: int
    bucket: Bucket
    counts: dict[str, int]
    top_blockers: tuple[Recommendation, ...]


def score_recommendations(
    recommendations: Iterable[Recommendation],
    *,
    top_k: int = 5,
) -> ReadinessScore:
    recs = list(recommendations)
    counts: dict[str, int] = {"blocker": 0, "warning": 0, "info": 0}
    for r in recs:
        counts[r.severity] = counts.get(r.severity, 0) + 1

    score = 100
    for severity, (weight, cap) in _SEVERITY_WEIGHT.items():
        deduction = min(counts.get(severity, 0) * weight, cap)
        score -= deduction
    score = max(0, min(100, score))

    if score >= 80:
        bucket: Bucket = "ready"
    elif score >= 50:
        bucket = "ready-with-effort"
    else:
        bucket = "blocked"

    ranked = sorted(
        recs,
        key=lambda r: (
            _SEVERITY_RANK.get(r.severity, 0),
            _EFFORT_RANK.get(r.effort, 0),
        ),
        reverse=True,
    )
    return ReadinessScore(
        score=score, bucket=bucket, counts=counts,
        top_blockers=tuple(ranked[:top_k]),
    )
