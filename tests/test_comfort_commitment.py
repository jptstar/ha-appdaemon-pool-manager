import datetime as dt
from pool_comfort import ComfortCommitment
from pool_predictive import normalize_daily_forecast, find_swim_opportunities


def rows(score=65, condition='sunny', temperature=27):
    result = normalize_daily_forecast([{'datetime': '2026-10-03T12:00:00',
                                       'temperature': temperature, 'templow': 12,
                                       'condition': condition}])
    result[0].update(score=score, usage_score=score, strategic_score=score)
    return result


def test_marginal_forecast_keeps_saturday_and_does_not_mutate_weather():
    commitment = ComfortCommitment()
    now = dt.datetime(2026, 10, 2, 10)
    commitment.prepare(rows(), now)
    revised = rows(48, 'partlycloudy', 23)
    prepared = commitment.prepare(revised, now+dt.timedelta(hours=2))
    assert commitment.date == dt.date(2026, 10, 3)
    assert prepared[0]['usage_score'] == 48
    assert prepared[0]['_comfort_committed']
    assert find_swim_opportunities(prepared, now.date())[0]['date'] == commitment.date
    assert revised[0]['usage_score'] == 48


def test_unreachable_commitment_is_reported_not_postponed():
    commitment = ComfortCommitment({'comfort_target_date': '2026-10-03'})
    commitment.prepare(rows(), dt.datetime(2026, 10, 3, 13))
    plan = commitment.annotate({'candidate': {'date': dt.date(2026,10,5)}},
                               dt.datetime(2026,10,3,13), 27,31,12)
    assert plan['candidate']['date'] == dt.date(2026,10,3)
    assert plan['comfort_status'] == 'late'
    assert dt.date(2026,10,3) in plan['missed_swim_dates']


def test_storm_cancels_explicitly_without_false_readiness():
    commitment = ComfortCommitment({'comfort_target_date': '2026-10-03'})
    commitment.prepare(rows(15, 'lightning-rainy'), dt.datetime(2026,10,2,16))
    assert commitment.status == 'cancelled_weather'
    plan = commitment.annotate({}, dt.datetime(2026,10,3,12), 31,31,12)
    assert plan['comfort_status'] == 'cancelled_weather'


def test_missing_forecast_preserves_target_and_reports_uncertainty():
    commitment = ComfortCommitment({'comfort_target_date': '2026-10-03'})
    commitment.prepare([], dt.datetime(2026,10,2,10))
    assert commitment.date == dt.date(2026,10,3)
    assert commitment.status == 'forecast_missing'


def test_ready_target_expires_after_usage_window_not_at_noon():
    commitment = ComfortCommitment({'comfort_target_date': '2026-10-03'})
    commitment.prepare(rows(), dt.datetime(2026,10,3,14))
    assert commitment.annotate({}, dt.datetime(2026,10,3,14),31,31,12)['comfort_status'] == 'ready'
    commitment.prepare([], dt.datetime(2026,10,3,20))
    assert commitment.date is None


def test_unreachable_tomorrow_remains_at_risk_and_recovers_today_not_at_night():
    commitment = ComfortCommitment({'comfort_target_date':'2026-10-03'})
    now = dt.datetime(2026,10,2,15)
    commitment.prepare(rows(), now)
    plan = commitment.annotate({'should_heat':False, 'action':'WAIT',
        'missed_swim_dates':[dt.date(2026,10,3)]}, now,26,31,12)
    assert plan['comfort_status'] == 'at_risk'
    day = commitment.recover_daylight(plan,now,26,31,daylight=True)
    assert day['should_heat'] and day['preset'] == 'Smart'
    assert commitment.recover_daylight(plan,now,26,31,daylight=False) == plan
    suspended = dict(plan,decision_choice='skip')
    assert commitment.recover_daylight(suspended,now,26,31,daylight=True) == suspended


def test_recovery_does_not_heat_three_days_early_or_after_weather_cancel():
    now=dt.datetime(2026,10,1,12)
    commitment=ComfortCommitment({'comfort_target_date':'2026-10-04'})
    commitment.status='at_risk'
    plan={'should_heat':False}
    assert commitment.recover_daylight(plan,now,26,31,daylight=True) == plan
    commitment.date=now.date()
    commitment.status='cancelled_weather'
    assert commitment.recover_daylight(plan,now,26,31,daylight=True) == plan
