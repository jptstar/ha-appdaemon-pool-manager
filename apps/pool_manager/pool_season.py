"""Pure end-of-season policy. No Home Assistant side effects."""
import datetime
from dataclasses import dataclass

@dataclass(frozen=True)
class SeasonDecision:
    state: str
    heat_allowed: bool
    permanent: bool
    reason: str

def decide_season(now, *, mode, mean_7d_c=None, cold_since=None,
                  forecast_scores=None, min_score=55, recoverable=True,
                  already_closed=False, override_until=None,
                  cutoff_month=10, cutoff_day=10):
    """Return a policy decision. The caller must persist permanent closure.

    A missing forecast never causes irreversible early closure.
    A manual override is permitted only when explicitly time-bounded; safety
    interlocks must still be enforced by the caller.
    """
    if mode != "end_season":
        return SeasonDecision("inactive", True, False, "Other heating mode")
    cutoff = datetime.date(now.year, cutoff_month, cutoff_day)
    closed_by_date = now.date() >= cutoff
    permanent = already_closed or closed_by_date
    if not permanent and mean_7d_c is not None and cold_since is not None:
        cold_hours = (now - cold_since).total_seconds() / 3600
        cold_confirmed = mean_7d_c < 16 and cold_hours >= 72
        no_bathing = bool(forecast_scores) and all(
            score < min_score for score in forecast_scores
        )
        permanent = cold_confirmed and no_bathing and not recoverable
    if override_until is not None and now < override_until:
        return SeasonDecision("manual_override", True, permanent, "Temporary manual override")
    if permanent:
        state = "closed_date" if closed_by_date else "closed_early"
        return SeasonDecision(state, False, True, "Season heating closed")
    if forecast_scores is not None and (
        not forecast_scores or max(forecast_scores) < min_score or not recoverable
    ):
        return SeasonDecision("paused_weather", False, False, "No viable bathing window")
    return SeasonDecision("active", True, False, "Bathing window possible")
