import datetime
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "apps" / "pool_manager" / "pool_predictive.py"
spec = importlib.util.spec_from_file_location("pool_predictive", MODULE_PATH)
pool_predictive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pool_predictive)


def _forecast(now, specs):
    raw = []
    for index, spec_item in enumerate(specs):
        temperature = spec_item[0]
        condition = spec_item[1]
        templow = spec_item[2] if len(spec_item) > 2 else temperature - 6
        rain = spec_item[3] if len(spec_item) > 3 else 0
        raw.append(
            {
                "datetime": (
                    datetime.datetime.combine(
                        now.date() + datetime.timedelta(days=index),
                        datetime.time(12, 0),
                    )
                ).isoformat(),
                "temperature": temperature,
                "templow": templow,
                "condition": condition,
                "precipitation_probability": rain,
                "precipitation": 0,
                "wind_speed": 5,
            }
        )
    return pool_predictive.normalize_daily_forecast(raw)


def _plan(now, forecast, water=24.0, target=30.0, **kwargs):
    defaults = {
        "base_heating_rate_c_per_h": 0.30,
        "day_hours": 10.0,
        "today_day_hours_remaining": 10.0,
        "candidate_day_hours": 5.0,
        "night_hours": 10.0,
        "daylight_active": True,
        "floor_delta_c": 5.0,
        "smart_preset": "Smart",
        "turbo_preset": "Turbo",
    }
    defaults.update(kwargs)
    return pool_predictive.build_predictive_plan(
        now=now,
        water_c=water,
        target_c=target,
        forecast=forecast,
        **defaults,
    )


def test_weather_score_prefers_warm_sunny_day():
    sunny = pool_predictive.swim_day_score(
        {"temperature": 25, "condition": "sunny", "wind_speed": 5}
    )
    rainy = pool_predictive.swim_day_score(
        {
            "temperature": 20,
            "condition": "rainy",
            "wind_speed": 20,
            "precipitation_probability": 90,
        }
    )
    assert sunny > rainy


def test_hourly_context_uses_after_work_window_on_weekday():
    # Friday 2026-09-18: warm daily maximum, but poor 16:00-20:00 conditions.
    day = datetime.date(2026, 9, 18)
    daily = pool_predictive.normalize_daily_forecast(
        [
            {
                "datetime": datetime.datetime.combine(day, datetime.time(12)).isoformat(),
                "temperature": 26,
                "templow": 15,
                "condition": "sunny",
            }
        ]
    )
    hourly = pool_predictive.normalize_hourly_forecast(
        [
            {
                "datetime": datetime.datetime.combine(day, datetime.time(hour)).isoformat(),
                "temperature": 25 if hour < 16 else 18,
                "condition": "sunny" if hour < 16 else "cloudy",
            }
            for hour in range(8, 20)
        ]
    )
    enriched = pool_predictive.enrich_daily_with_hourly(daily, hourly)
    assert enriched[0]["usage_window"] == "16:00-20:00"
    assert enriched[0]["usage_temperature"] == 18.0
    opportunities = pool_predictive.find_swim_opportunities(
        enriched,
        day,
        score_min=55,
        min_air_c=21,
    )
    assert opportunities == []


def test_weekend_gets_usage_priority_over_marginal_friday():
    # Friday + Saturday. Saturday is only modestly better but is the more useful
    # bathing day and receives the weekend bonus.
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (22.0, "sunny", 15),
            (23.0, "sunny", 16),
        ],
    )
    opportunities = pool_predictive.find_swim_opportunities(
        forecast,
        now,
        score_min=55,
        min_air_c=21,
    )
    assert opportunities
    assert opportunities[0]["date"].weekday() == 5
    assert opportunities[0]["weekend"] is True


def test_bad_weather_above_floor_keeps_pac_off():
    now = datetime.datetime(2026, 9, 18, 10, 0)
    forecast = _forecast(
        now,
        [
            (15, "rainy", 10, 90),
            (14, "rainy", 9, 90),
            (16, "cloudy", 10, 70),
        ],
    )
    plan = _plan(
        now,
        forecast,
        water=25,
        target=30,
        minimum_water_c=22,
    )
    assert plan["candidate"] is None
    assert plan["action"] == "WAIT"
    assert plan["should_heat"] is False


def test_floor_protection_heats_only_reserve_not_full_setpoint():
    now = datetime.datetime(2026, 9, 18, 10, 0)
    forecast = _forecast(
        now,
        [
            (12, "rainy", 5, 90),
            (13, "rainy", 6, 90),
        ],
    )
    plan = _plan(
        now,
        forecast,
        water=21.7,
        target=30,
        minimum_water_c=22,
    )
    assert plan["action"] == "PRESERVE"
    assert plan["should_heat"] is True
    assert 22 < plan["heat_target_c"] < 30


def test_future_good_day_creates_trajectory_not_immediate_full_heat():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (17, "cloudy", 10),
            (19, "cloudy", 12),
            (25, "sunny", 17),
        ],
    )
    learned = {
        "smart": {
            "15_20": {"rate": 0.40, "count": 20},
            "20_25": {"rate": 0.45, "count": 20},
        }
    }
    plan = _plan(
        now,
        forecast,
        water=24,
        target=30,
        heating_rate_model=learned,
        minimum_water_c=22,
    )
    assert plan["candidate"] is not None
    assert plan["candidate"]["date"] == now.date() + datetime.timedelta(days=2)
    assert plan["trajectory_target_c"] < 30
    if plan["should_heat"]:
        assert plan["heat_target_c"] == plan["trajectory_target_c"]


def test_smart_is_used_when_today_capacity_is_enough():
    now = datetime.datetime(2026, 9, 18, 9, 0)
    forecast = _forecast(
        now,
        [
            (18, "cloudy", 12),
            (25, "sunny", 17),
        ],
    )
    learned = {
        "smart": {"15_20": {"rate": 0.50, "count": 20}},
        "turbo": {"15_20": {"rate": 0.75, "count": 20}},
    }
    plan = _plan(
        now,
        forecast,
        water=24,
        target=30,
        heating_rate_model=learned,
        today_day_hours_remaining=8,
    )
    if plan["should_heat"]:
        assert plan["preset"] == "Smart"


def test_turbo_is_selected_when_smart_day_capacity_is_not_enough():
    now = datetime.datetime(2026, 9, 18, 9, 0)
    forecast = _forecast(
        now,
        [
            (18, "cloudy", 12),
            (23, "sunny", 15),
        ],
    )
    learned = {
        "smart": {
            "15_20": {"rate": 0.20, "count": 30},
            "20_25": {"rate": 0.20, "count": 30},
        },
        "turbo": {
            "15_20": {"rate": 0.55, "count": 30},
            "20_25": {"rate": 0.55, "count": 30},
        },
    }
    plan = _plan(
        now,
        forecast,
        water=23,
        target=30,
        heating_rate_model=learned,
        today_day_hours_remaining=7,
        candidate_day_hours=2,
        minimum_water_c=22,
    )
    assert plan["candidate"] is not None
    assert plan["should_heat"] is True
    assert plan["preset"] == "Turbo"


def test_night_heat_is_only_requested_when_day_cannot_hold_trajectory():
    now = datetime.datetime(2026, 9, 18, 21, 0)
    forecast = _forecast(
        now,
        [
            (17, "cloudy", 10),
            (23, "sunny", 14),
        ],
    )
    learned = {
        "smart": {
            "10_15": {"rate": 0.25, "count": 20},
            "15_20": {"rate": 0.25, "count": 20},
            "20_25": {"rate": 0.25, "count": 20},
        },
        "turbo": {
            "10_15": {"rate": 0.45, "count": 20},
            "15_20": {"rate": 0.45, "count": 20},
            "20_25": {"rate": 0.45, "count": 20},
        },
    }
    plan = _plan(
        now,
        forecast,
        water=22.5,
        target=30,
        heating_rate_model=learned,
        today_day_hours_remaining=0,
        candidate_day_hours=2,
        daylight_active=False,
        minimum_water_c=22,
    )
    assert plan["candidate"] is not None
    if plan["should_heat"]:
        assert plan["night_heating"] is True
        assert plan["heat_target_c"] <= 30


def test_unrecoverable_today_is_skipped():
    now = datetime.datetime(2026, 9, 18, 9, 0)
    forecast = _forecast(
        now,
        [
            (24, "sunny", 16),
            (25, "sunny", 17),
        ],
    )
    very_slow = {
        "smart": {"20_25": {"rate": 0.10, "count": 30}},
        "turbo": {"20_25": {"rate": 0.15, "count": 30}},
        "turbo": {
            "20_25": {"rate": 0.15, "count": 30},
            "15_20": {"rate": 0.15, "count": 30},
        },
    }
    plan = _plan(
        now,
        forecast,
        water=18,
        target=30,
        heating_rate_model=very_slow,
        today_day_hours_remaining=5,
        night_hours=8,
    )
    assert plan["candidate"] is None or plan["candidate"]["date"] != now.date()


def test_heating_learning_keeps_power_and_energy_per_degree():
    model = pool_predictive.update_heating_rate_model(
        {},
        "Smart",
        18,
        0.30,
        alpha=1.0,
        sample_power_w=1200,
    )
    entry = model["smart"]["15_20"]
    assert entry["rate"] == 0.30
    assert entry["power_w"] == 1200
    assert entry["kwh_per_c"] == 4.0


def test_loss_learning_separates_cover_and_delta():
    model = pool_predictive.update_loss_model(
        {},
        "closed",
        water_c=28,
        ambient_c=13,
        sample_loss_c_per_h=0.08,
        alpha=1.0,
    )
    assert model["closed"]["15_20"]["rate"] == 0.08
    assert pool_predictive.estimate_loss_rate(
        28,
        13,
        cover_state="closed",
        learned_model=model,
    ) > 0


def test_dashboard_marks_selected_day_and_recovery_window():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (18, "cloudy", 12),
            (19, "cloudy", 13),
            (25, "sunny", 16),
        ],
    )
    plan = _plan(now, forecast, water=24, target=30)
    rows = pool_predictive.dashboard_forecast(forecast, plan, now)
    assert len(rows) == 3
    if plan["candidate"] is not None:
        assert any(row["swim"] for row in rows)


def test_fifteen_day_outlook_remains_visible():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    specs = [(15, "cloudy", 10)] * 10 + [(26, "sunny", 18)] + [(15, "rainy", 10)] * 4
    forecast = _forecast(now, specs)
    assert len(forecast) == 15
    plan = _plan(now, forecast, water=26.5, target=30)
    rows = pool_predictive.dashboard_forecast(forecast, plan, now)
    assert len(rows) == 15
