import datetime as dt
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "apps" / "pool_manager"))
from pool_season import decide_season

T = dt.datetime(2026, 9, 25, 12)

def policy(now=T, **kw):
    defaults = dict(
        mode="end_season", forecast_scores=[30.0] * 15,
        recoverable=False, mean_7d_c=15.0,
        cold_since=now-dt.timedelta(hours=73),
    )
    defaults.update(kw)
    return decide_season(now, **defaults)

def test_cold_weather_recommends_but_does_not_lock():
    d = policy()
    assert d.state == "recommend_closure"
    assert d.closure_recommended and not d.permanent and not d.heat_allowed

def test_short_cold_snap_is_reversible():
    d = policy(cold_since=T-dt.timedelta(hours=48))
    assert d.state == "paused_weather" and not d.permanent

def test_warm_weekend_restores_heat_even_after_cold():
    d = policy(forecast_scores=[30]*14+[75], viable_window=True, recoverable=True)
    assert d.state == "active" and d.heat_allowed

@pytest.mark.parametrize("hour,closed", [(dt.datetime(2026,10,9,23,59),False),
                                         (dt.datetime(2026,10,10,0,0),True),
                                         (dt.datetime(2026,11,1,10,0),True)])
def test_calendar_boundary(hour,closed):
    d = policy(now=hour, mean_7d_c=20, cold_since=None)
    assert d.permanent == closed

def test_no_reactivation_after_restart():
    d = policy(now=dt.datetime(2027,3,1),already_closed=True,mode="auto")
    assert d.permanent and not d.heat_allowed

def test_manual_confirmation_closes_prior_to_deadline():
    assert policy(already_closed=True).state == "closed_manual"

def test_override_is_temporary():
    assert policy(override_until=T+dt.timedelta(hours=1)).heat_allowed
    assert not policy(override_until=T-dt.timedelta(seconds=1)).heat_allowed

def test_override_never_bypasses_october_cutoff():
    now=dt.datetime(2026,10,31,0)
    assert not policy(now=now,override_until=now+dt.timedelta(days=1)).heat_allowed

def test_missing_forecast_never_closes_permanently():
    d=policy(forecast_ok=False,forecast_scores=None)
    assert d.state == "paused_weather" and not d.permanent

def test_other_heating_modes_unaffected_until_locked():
    assert policy(mode="turbo",mean_7d_c=12).heat_allowed
    assert not policy(mode="turbo",already_closed=True).heat_allowed

def test_bathing_score_alone_not_enough_if_unreachable():
    assert policy(forecast_scores=[75],recoverable=False).state=="recommend_closure"

def test_forecast_without_viable_window_pauses_preservation():
    assert policy(forecast_scores=[80],viable_window=False).state=="recommend_closure"

def test_weather_threshold_is_strict():
    assert policy(mean_7d_c=16.0).state=="paused_weather"

def test_future_cold_since_does_not_recommend():
    assert policy(cold_since=T+dt.timedelta(hours=2)).state=="paused_weather"

def test_no_season_policy_in_other_mode_before_cutoff():
    assert policy(mode="auto",forecast_ok=False,forecast_scores=None).heat_allowed
