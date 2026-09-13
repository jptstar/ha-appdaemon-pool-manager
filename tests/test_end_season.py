import datetime
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "apps" / "pool_manager" / "pool_end_season.py"
spec = importlib.util.spec_from_file_location("pool_end_season", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _forecast(now, values):
    days = []
    for offset, (temp, condition, rain) in enumerate(values):
        day = now.date() + datetime.timedelta(days=offset)
        days.append(
            {
                "datetime": f"{day.isoformat()}T00:00:00+02:00",
                "temperature": temp,
                "condition": condition,
                "precipitation_probability": rain,
                "wind_speed": 8,
            }
        )
    return module.normalize_daily_forecast(days, horizon_days=10)


def test_sunny_warm_day_scores_above_rainy_cold_day():
    sunny = module.swim_day_score(
        {"temperature": 25, "condition": "sunny", "precipitation_probability": 0}
    )
    rainy = module.swim_day_score(
        {"temperature": 18, "condition": "rainy", "precipitation_probability": 90}
    )
    assert sunny > 70
    assert rainy < 30


def test_hot_tomorrow_does_not_trigger_needless_night_heating():
    now = datetime.datetime(2026, 9, 13, 22, 0)
    forecast = _forecast(
        now,
        [
            (17, "rainy", 80),
            (26, "sunny", 0),
            (25, "partlycloudy", 10),
            (24, "partlycloudy", 10),
        ],
    )

    plan = module.build_end_season_plan(
        now=now,
        water_c=29.0,
        target_c=30.0,
        forecast=forecast,
        heating_rate_c_per_h=0.30,
        swim_time="16:00:00",
    )

    assert plan["candidate"]["index"] == 1
    assert plan["should_heat"] is False
    assert plan["planned_start"].date() == datetime.date(2026, 9, 14)
    assert plan["planned_start"].hour >= 11


def test_last_bathing_day_before_three_bad_days_is_detected():
    now = datetime.datetime(2026, 9, 13, 9, 0)
    forecast = _forecast(
        now,
        [
            (18, "cloudy", 20),
            (25, "sunny", 0),
            (15, "rainy", 90),
            (14, "rainy", 90),
            (16, "cloudy", 70),
        ],
    )

    candidate = module.find_next_swim_window(forecast, now)

    assert candidate is not None
    assert candidate["index"] == 1
    assert candidate["last_chance"] is True
    assert candidate["bad_streak_after"] >= 2


def test_ten_day_horizon_can_see_late_bathing_window():
    now = datetime.datetime(2026, 9, 13, 9, 0)
    values = [(16, "rainy", 80)] * 8 + [(25, "sunny", 0), (17, "rainy", 80)]
    forecast = _forecast(now, values)

    candidate = module.find_next_swim_window(forecast, now)

    assert candidate is not None
    assert candidate["index"] == 8


def test_no_good_weather_keeps_only_recovery_floor():
    now = datetime.datetime(2026, 9, 13, 11, 0)
    forecast = _forecast(now, [(16, "rainy", 80)] * 10)

    plan = module.build_end_season_plan(
        now=now,
        water_c=28.0,
        target_c=30.0,
        forecast=forecast,
        floor_delta_c=3.0,
    )

    assert plan["candidate"] is None
    assert plan["should_heat"] is False
    assert plan["floor_c"] == 27.0


def test_below_floor_recharges_only_during_preferred_daytime():
    forecast = []
    daytime = datetime.datetime(2026, 9, 13, 12, 0)
    night = datetime.datetime(2026, 9, 13, 23, 0)

    day_plan = module.build_end_season_plan(
        now=daytime,
        water_c=26.5,
        target_c=30.0,
        forecast=forecast,
        floor_delta_c=3.0,
    )
    night_plan = module.build_end_season_plan(
        now=night,
        water_c=26.5,
        target_c=30.0,
        forecast=forecast,
        floor_delta_c=3.0,
    )

    assert day_plan["should_heat"] is True
    assert day_plan["heat_target_c"] == 27.5
    assert night_plan["should_heat"] is False


def test_overdue_deadline_can_force_heating_outside_preferred_window():
    now = datetime.datetime(2026, 9, 13, 6, 30)
    forecast = _forecast(now, [(25, "sunny", 0), (15, "rainy", 90), (14, "rainy", 90)])

    # 3 °C at 0.30 °C/h requires about 10 h. The preferred 08:00-16:00 period
    # cannot provide enough heat, so the backward planner starts the previous
    # evening and the 06:30 decision is already overdue.
    plan = module.build_end_season_plan(
        now=now,
        water_c=27.0,
        target_c=30.0,
        forecast=forecast,
        heating_rate_c_per_h=0.30,
        swim_time="16:00:00",
    )

    assert plan["candidate"]["index"] == 0
    assert plan["candidate"]["last_chance"] is True
    assert plan["should_heat"] is True
    assert plan["heat_target_c"] == 30.0


def test_extract_forecast_supports_direct_and_wrapped_appdaemon_response():
    forecast = [{"temperature": 25}]
    direct = {"weather.pool": {"forecast": forecast}}
    wrapped = {"result": {"response": direct}}

    assert module.extract_weather_forecast(direct, "weather.pool") == forecast
    assert module.extract_weather_forecast(wrapped, "weather.pool") == forecast
