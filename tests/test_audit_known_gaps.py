"""Regression reproductions discovered in the v0.10.15 component audit."""
import datetime
from test_mpc import _plan, _forecast
from test_pool_decision import App
from test_predictive_runtime_learning import _make_runtime


def test_unmet_saturday_remains_visible_after_noon():
    now=datetime.datetime(2026,10,3,10)
    forecast=_forecast(now,[(30,'sunny',20)]*3)
    before=_plan(now,forecast,water_c=30.7,target_c=31,swim_hour=12)
    assert now.date() in before['swim_dates']
    after=_plan(now.replace(hour=12),forecast,water_c=30.7,target_c=31,swim_hour=12)
    assert now.date() in after.get('swim_dates',[]) + after.get('missed_swim_dates',[])


def test_one_minute_old_certification_is_reused(tmp_path):
    app=_make_runtime(tmp_path)
    now=datetime.datetime.now()
    app.chauffage_mode=lambda:'Fin de saison • Smart'
    app.chauffage_predictif_certified_water_c=29
    app.chauffage_predictif_certified_at=now-datetime.timedelta(minutes=1)
    app.last_pompe_on=now-datetime.timedelta(seconds=10)
    app.last_pompe_off=now-datetime.timedelta(seconds=40)
    app._update_certified_measurement(now)
    assert not app.chauffage_predictif_measurement_active


def test_unknown_stop_duration_still_requires_calibration(tmp_path):
    app = _make_runtime(tmp_path)
    now = datetime.datetime.now()
    app.chauffage_mode = lambda: 'Fin de saison • Smart'
    app.chauffage_predictif_certified_water_c = 29
    app.chauffage_predictif_certified_at = now-datetime.timedelta(minutes=1)
    app.last_pompe_on = now-datetime.timedelta(seconds=10)
    app._update_certified_measurement(now)
    assert app.chauffage_predictif_measurement_active


def test_night_uses_observed_open_cover_not_expected_closed(tmp_path):
    app = _make_runtime(tmp_path)
    app.cover = 'open'
    app.chauffage_predictif_volet_nuit_prevu = 'closed'
    app._predictive_daylight_active = lambda: False
    assert app._predictive_expected_night_cover_state() == 'open'


def test_cancelled_bathing_target_is_not_forced_by_night_permission():
    app = App()
    app.daylight = False
    now = datetime.datetime.now()
    app._pool_decision_choice = 'night'
    app._pool_decision_choice_until = now+datetime.timedelta(hours=2)
    result = app._apply_pool_decision({'should_heat': False, 'comfort_status': 'cancelled_weather'},
                                      27,31,now=now)
    assert not result['should_heat']


def test_economy_recovery_keeps_smart_preset():
    app=App(); now=datetime.datetime.now()
    app._pool_decision_choice='eco'
    app._pool_decision_day=now.date()
    app._pool_decision_choice_until=now+datetime.timedelta(hours=2)
    result=app._apply_pool_decision({'should_heat':False,'missed_swim_dates':[now.date()]},26,31,now=now)
    assert result['should_heat']
    assert result['preset']=='Smart'


def test_missing_day_is_not_silently_skipped():
    now=datetime.datetime(2026,10,3,10)
    forecast=_forecast(now,[(30,'sunny',20)]*3)
    forecast.pop(1)
    result=_plan(now,forecast,water_c=30,target_c=31)
    path=result.get('mpc_plan',[])
    assert path
    assert all((b['date']-a['date']).days==1 for a,b in zip(path,path[1:]))


def test_no_night_permission_is_respected_by_fallback():
    now=datetime.datetime(2026,10,3,23)
    result=_plan(now,_forecast(now,[(14,'cloudy',5)]*3),water_c=18,target_c=31,
                 minimum_water_c=22,allow_night_heating=False,daylight_active=False)
    assert not result['should_heat']
