import datetime
import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "apps" / "pool_manager" / "pool_predictive.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("pool_predictive", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _day(date, temp, condition="sunny", rain=0, wind=5):
    return {
        "datetime": f"{date.isoformat()}T12:00:00+02:00",
        "temperature": temp,
        "condition": condition,
        "precipitation_probability": rain,
        "wind_speed": wind,
    }


def _forecast(now, specs):
    raw = []
    for offset, spec in enumerate(specs):
        temp, condition, rain, wind = spec
        raw.append(
            _day(
                now.date() + datetime.timedelta(days=offset),
                temp,
                condition,
                rain,
                wind,
            )
        )
    return module.normalize_daily_forecast(raw, horizon_days=10)


def test_sunny_warm_day_scores_above_rainy_windy_day():
    sunny = module.swim_day_score(
        {"temperature": 26, "condition": "sunny", "precipitation_probability": 5, "wind_speed": 5}
    )
    bad = module.swim_day_score(
        {"temperature": 23, "condition": "rainy", "precipitation_probability": 90, "wind_speed": 30}
    )
    assert sunny > 80
    assert bad < sunny


def test_good_day_followed_by_bad_spell_is_last_chance():
    now = datetime.datetime(2026, 9, 13, 10, 0)
    forecast = _forecast(
        now,
        [
            (18, "rainy", 80, 10),
            (26, "sunny", 5, 5),
            (16, "rainy", 90, 15),
            (15, "rainy", 90, 15),
            (16, "cloudy", 60, 10),
        ],
    )
    opportunities = module.find_swim_opportunities(forecast, now)
    assert opportunities
    assert opportunities[0]["index"] == 1
    assert opportunities[0]["last_chance"] is True


def test_small_recovery_is_planned_the_previous_day():
    now = datetime.datetime(2026, 9, 13, 10, 0)
    ready = datetime.datetime(2026, 9, 15, 11, 0)
    swim = datetime.datetime(2026, 9, 15, 16, 0)
    slots = module.build_heating_schedule(
        now=now,
        ready_datetime=ready,
        swim_datetime=swim,
        required_hours=2.0,
        previous_day_start="12:00:00",
        previous_day_end="20:00:00",
        morning_start="07:00:00",
    )
    assert len(slots) == 1
    assert slots[0]["kind"] == "previous_day"
    assert slots[0]["start"] == datetime.datetime(2026, 9, 14, 18, 0)
    assert slots[0]["end"] == datetime.datetime(2026, 9, 14, 20, 0)


def test_large_recovery_splits_previous_day_and_morning_topup():
    now = datetime.datetime(2026, 9, 13, 10, 0)
    ready = datetime.datetime(2026, 9, 15, 11, 0)
    swim = datetime.datetime(2026, 9, 15, 16, 0)
    slots = module.build_heating_schedule(
        now=now,
        ready_datetime=ready,
        swim_datetime=swim,
        required_hours=10.0,
    )
    kinds = {slot["kind"] for slot in slots}
    assert "previous_day" in kinds
    assert "morning_topup" in kinds
    previous = next(slot for slot in slots if slot["kind"] == "previous_day")
    morning = next(slot for slot in slots if slot["kind"] == "morning_topup")
    assert previous["hours"] == 8.0
    assert morning["hours"] == 2.0


def test_ten_bad_days_do_not_hold_full_setpoint_in_automatic_profile():
    now = datetime.datetime(2026, 7, 10, 14, 0)
    forecast = _forecast(now, [(17, "rainy", 90, 20)] * 10)
    plan = module.build_predictive_plan(
        now=now,
        water_c=28.5,
        target_c=30.0,
        forecast=forecast,
        heating_rate_c_per_h=0.30,
        floor_delta_c=2.0,
    )
    assert plan["candidate"] is None
    assert plan["should_heat"] is False
    assert plan["floor_c"] == 28.0


def test_bad_spell_recharges_only_dynamic_reserve_when_pool_too_cold():
    now = datetime.datetime(2026, 7, 10, 14, 0)
    forecast = _forecast(now, [(17, "rainy", 90, 20)] * 10)
    plan = module.build_predictive_plan(
        now=now,
        water_c=27.4,
        target_c=30.0,
        forecast=forecast,
        heating_rate_c_per_h=0.30,
        floor_delta_c=2.0,
        previous_day_start="12:00:00",
        previous_day_end="20:00:00",
    )
    assert plan["should_heat"] is True
    assert plan["heat_target_c"] == 28.5


def test_future_swim_day_creates_previous_day_preheat_plan():
    now = datetime.datetime(2026, 9, 13, 10, 0)
    forecast = _forecast(
        now,
        [
            (18, "rainy", 80, 10),
            (19, "cloudy", 50, 10),
            (26, "sunny", 5, 5),
            (25, "sunny", 5, 5),
        ],
    )
    plan = module.build_predictive_plan(
        now=now,
        water_c=28.5,
        target_c=30.0,
        forecast=forecast,
        heating_rate_c_per_h=0.30,
        floor_delta_c=2.0,
        ready_time="11:00:00",
    )
    assert plan["candidate"]["index"] == 2
    assert plan["next_segment"] is not None
    assert plan["next_segment"]["start"].date() == now.date() + datetime.timedelta(days=1)
    assert plan["should_heat"] is False


def test_plan_heats_when_current_time_is_inside_scheduled_slot():
    now = datetime.datetime(2026, 9, 14, 18, 30)
    forecast = _forecast(
        now,
        [
            (22, "partlycloudy", 20, 5),
            (27, "sunny", 0, 5),
            (16, "rainy", 90, 15),
            (15, "rainy", 90, 15),
        ],
    )
    plan = module.build_predictive_plan(
        now=now,
        water_c=29.0,
        target_c=30.0,
        forecast=forecast,
        heating_rate_c_per_h=0.40,
        floor_delta_c=2.0,
        ready_time="11:00:00",
        previous_day_start="12:00:00",
        previous_day_end="20:00:00",
    )
    assert plan["candidate"]["index"] == 1
    assert plan["should_heat"] is True
    assert plan["active_segment"] is not None


def test_end_of_season_profile_allows_lower_reserve_than_auto():
    now = datetime.datetime(2026, 9, 13, 14, 0)
    forecast = _forecast(now, [(17, "rainy", 90, 20)] * 7)
    auto = module.build_predictive_plan(
        now=now,
        water_c=28.5,
        target_c=30.0,
        forecast=forecast,
        heating_rate_c_per_h=0.30,
        floor_delta_c=2.0,
    )
    end = module.build_predictive_plan(
        now=now,
        water_c=28.5,
        target_c=30.0,
        forecast=forecast,
        heating_rate_c_per_h=0.30,
        floor_delta_c=4.0,
    )
    assert auto["floor_c"] == 28.0
    assert end["floor_c"] == 26.0


def test_heating_rate_uses_outdoor_temperature_and_learning():
    cold = module.estimate_heating_rate(0.30, ambient_c=5)
    warm = module.estimate_heating_rate(0.30, ambient_c=27)
    assert cold < warm

    model = module.update_heating_rate_model({}, 27, 0.42)
    learned = module.estimate_heating_rate(0.30, ambient_c=27, learned_model=model)
    assert learned > warm


def test_dashboard_rows_mark_swim_day_and_heating_day():
    now = datetime.datetime(2026, 9, 13, 10, 0)
    forecast = _forecast(
        now,
        [
            (18, "rainy", 80, 10),
            (24, "sunny", 5, 5),
            (27, "sunny", 0, 5),
        ],
    )
    plan = module.build_predictive_plan(
        now=now,
        water_c=29.0,
        target_c=30.0,
        forecast=forecast,
        heating_rate_c_per_h=0.30,
        floor_delta_c=2.0,
    )
    rows = module.dashboard_forecast(forecast, plan, now)
    assert any(row["swim"] for row in rows)
    assert any(row["heating"] for row in rows)


def test_weather_quality_can_shift_limited_preheat_earlier():
    now = datetime.datetime(2026, 9, 13, 10, 0)
    forecast = _forecast(
        now,
        [
            (20, "cloudy", 40, 8),
            (27, "sunny", 0, 5),
            (15, "rainy", 90, 20),
            (27, "sunny", 0, 5),
        ],
    )
    ready = datetime.datetime(2026, 9, 16, 11, 0)
    swim = datetime.datetime(2026, 9, 16, 16, 0)
    slots = module.build_heating_schedule(
        now=now,
        ready_datetime=ready,
        swim_datetime=swim,
        required_hours=8.0,
        forecast=forecast,
    )
    assert any(slot["kind"] == "weather_preheat" for slot in slots)
    assert any(slot["kind"] == "previous_day" for slot in slots)
    smart = next(slot for slot in slots if slot["kind"] == "weather_preheat")
    assert smart["hours"] <= 2.0
