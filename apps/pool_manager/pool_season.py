# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Deterministic heating-only hybrid end-of-season decisions.

No pump, filtration or freeze-protection policy is represented here.
"""
import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class SeasonDecision:
    state: str
    heat_allowed: bool
    permanent: bool
    reason: str
    closure_recommended: bool = False


def decide_season(
    now, *, mode, mean_7d_c=None, cold_since=None, forecast_scores=None,
    min_score=55.0, recoverable=True, viable_window=None, forecast_ok=True,
    already_closed=False, override_until=None, cutoff_month=10, cutoff_day=31,
    cold_threshold_c=16.0, cold_confirmation_h=72.0,
):
    """Pure heating decision; an early recommendation is NOT a closure.

    The October cutoff applies to the selected end-season strategy. A
    previously latched closure remains effective in all heating strategies
    until explicitly reset at season start. Unknown weather never confirms
    an early closure. Temporary overrides cannot bypass the calendar cutoff.
    """
    date_cutoff = now.date() >= datetime.date(now.year, cutoff_month, cutoff_day)
    if already_closed:
        return SeasonDecision(
            "closed_date" if date_cutoff else "closed_manual",
            False, True, "Chauffage saisonnier verrouillé",
        )
    if mode != "end_season":
        return SeasonDecision("inactive", True, False, "Autre mode chauffage")
    if date_cutoff:
        return SeasonDecision("closed_date", False, True, "Date limite de saison atteinte")

    if override_until is not None and now < override_until:
        return SeasonDecision(
            "manual_override", True, False, "Dérogation de chauffe temporaire"
        )

    if not forecast_ok or forecast_scores is None:
        return SeasonDecision(
            "paused_weather", False, False, "Prévisions absentes ou périmées"
        )

    if viable_window is None:
        viable_window = bool(forecast_scores) and recoverable and any(
            score >= min_score for score in forecast_scores
        )
    if viable_window:
        return SeasonDecision("active", True, False, "Baignade atteignable prévue")

    cold_confirmed = (
        mean_7d_c is not None
        and cold_since is not None
        and mean_7d_c < cold_threshold_c
        and 0 <= (now - cold_since).total_seconds()
        and (now - cold_since).total_seconds() >= cold_confirmation_h * 3600
    )
    if cold_confirmed:
        return SeasonDecision(
            "recommend_closure", False, False,
            "Aucune baignade réalisable et refroidissement confirmé",
            closure_recommended=True,
        )
    return SeasonDecision(
        "paused_weather", False, False, "Aucune baignade atteignable"
    )
