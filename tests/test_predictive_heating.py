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


def _plan(now, forecast, water=24.0, target=28.0, **kwargs):
    defaults = {
        "base_heating_rate_c_per_h": 0.30,
        "day_hours": 12.0,
        "today_day_hours_remaining": 12.0,
        "night_hours": 12.0,
        "daylight_active": True,
        "floor_delta_c": 4.0,
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


def test_no_synthetic_bathing_hour_today_remains_a_calendar_day():
    now = datetime.datetime(2026, 9, 18, 20, 30)
    forecast = _forecast(now, [(24, "sunny", 15)])
    opportunities = pool_predictive.find_swim_opportunities(
        forecast,
        now,
        score_min=55,
        min_air_c=21,
    )
    assert opportunities
    assert opportunities[0]["date"] == now.date()


def test_unrecoverable_today_is_skipped_for_next_recoverable_good_day():
    now = datetime.datetime(2026, 9, 18, 6, 30)
    forecast = _forecast(
        now,
        [
            (21.2, "sunny", 12),
            (23.6, "sunny", 14),
            (25.2, "partlycloudy", 16),
        ],
    )
    plan = _plan(
        now,
        forecast,
        water=18.2,
        target=28.0,
        today_day_hours_remaining=12.0,
        daylight_active=False,
    )
    assert plan["candidate"]["date"] > now.date()
    assert plan["candidate"]["date"] == now.date() + datetime.timedelta(days=2)


def test_fast_learned_smart_rate_allows_later_start_without_night():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (18, "cloudy", 12),
            (20, "partlycloudy", 13),
            (25, "sunny", 16),
        ],
    )
    learned = {
        "smart": {
            "15_20": {"rate": 0.55, "count": 20},
            "20_25": {"rate": 0.60, "count": 20},
        }
    }
    plan = _plan(
        now,
        forecast,
        water=24.5,
        target=28,
        heating_rate_model=learned,
    )
    assert plan["candidate"]["date"] == now.date() + datetime.timedelta(days=2)
    assert plan["preset"].casefold() == "smart"
    assert plan["allow_night"] is False


def test_slow_recovery_can_authorize_night_heating():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (17, "cloudy", 9),
            (18, "cloudy", 10),
            (25, "sunny", 14),
        ],
    )
    slow = {
        "smart": {
            "15_20": {"rate": 0.18, "count": 20},
            "10_15": {"rate": 0.15, "count": 20},
        },
        "turbo": {
            "15_20": {"rate": 0.25, "count": 20},
            "10_15": {"rate": 0.22, "count": 20},
        },
    }
    plan = _plan(
        now,
        forecast,
        water=21.0,
        target=28.0,
        heating_rate_model=slow,
    )
    assert plan["candidate"] is not None
    assert plan["allow_night"] is True


def test_turbo_is_selected_before_night_when_smart_daytime_is_insufficient():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (20, "cloudy", 12),
            (25, "sunny", 15),
        ],
    )
    learned = {
        "smart": {"20_25": {"rate": 0.20, "count": 30}},
        "turbo": {"20_25": {"rate": 0.55, "count": 30}},
    }
    plan = _plan(
        now,
        forecast,
        water=24.5,
        target=28.0,
        heating_rate_model=learned,
        today_day_hours_remaining=10,
    )
    assert plan["candidate"]["date"] == now.date() + datetime.timedelta(days=1)
    assert plan["preset"].casefold() == "turbo"
    assert plan["allow_night"] is False


def test_night_loss_learning_increases_recovery_need():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (18, "cloudy", 8),
            (19, "cloudy", 9),
            (25, "sunny", 12),
        ],
    )
    no_loss = _plan(now, forecast, water=25, target=28)
    learned_loss = {
        "closed": {
            "15_20": {"rate": 0.10, "count": 20},
            "10_15": {"rate": 0.12, "count": 20},
        }
    }
    with_loss = _plan(
        now,
        forecast,
        water=25,
        target=28,
        loss_model=learned_loss,
        cover_state="closed",
    )
    assert with_loss["predicted_loss_c"] >= no_loss["predicted_loss_c"]
    assert with_loss["required_gain_c"] >= no_loss["required_gain_c"]


def test_absolute_minimum_water_temperature_protects_recoverability():
    now = datetime.datetime(2026, 9, 18, 10, 0)
    forecast = _forecast(
        now,
        [
            (14, "rainy", 7, 90),
            (13, "rainy", 6, 90),
            (14, "cloudy", 7, 60),
        ],
    )
    plan = _plan(
        now,
        forecast,
        water=21.7,
        target=28,
        minimum_water_c=22.0,
        daylight_active=True,
    )
    assert plan["candidate"] is None
    assert plan["floor_c"] == 22.0
    assert plan["should_heat"] is True
    assert plan["heat_target_c"] > 22.0


def test_bad_weather_above_floor_switches_pac_off():
    now = datetime.datetime(2026, 9, 18, 10, 0)
    forecast = _forecast(
        now,
        [
            (15, "rainy", 12, 90),
            (14, "rainy", 12, 90),
            (16, "cloudy", 13, 70),
        ],
    )
    plan = _plan(
        now,
        forecast,
        water=25.0,
        target=28,
        minimum_water_c=22.0,
    )
    assert plan["candidate"] is None
    assert plan["should_heat"] is False


def test_heating_rate_learning_is_split_by_pac_preset():
    model = {}
    model = pool_predictive.update_heating_rate_model(
        model, "Smart", 18, 0.30, alpha=1.0
    )
    model = pool_predictive.update_heating_rate_model(
        model, "Turbo", 18, 0.50, alpha=1.0
    )
    assert model["smart"]["15_20"]["rate"] == 0.30
    assert model["turbo"]["15_20"]["rate"] == 0.50


def test_night_loss_learning_is_split_by_cover_and_thermal_delta():
    model = {}
    model = pool_predictive.update_loss_model(
        model,
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


def test_dashboard_marks_swim_day_and_preheat_days():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (18, "cloudy", 12),
            (19, "cloudy", 13),
            (25, "sunny", 16),
        ],
    )
    plan = _plan(now, forecast, water=24.0, target=28)
    rows = pool_predictive.dashboard_forecast(forecast, plan, now)
    assert len(rows) == 3
    assert any(row["swim"] for row in rows)
    swim_index = next(i for i, row in enumerate(rows) if row["swim"])
    assert rows[swim_index]["date"] == plan["candidate"]["date"].isoformat()


def test_fifteen_day_outlook_remains_visible():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    specs = (
        [(15, "cloudy", 10)] * 10
        + [(26, "sunny", 18)]
        + [(15, "rainy", 10)] * 4
    )
    forecast = _forecast(now, specs)
    assert len(forecast) == 15
    plan = _plan(now, forecast, water=26.5, target=28)
    rows = pool_predictive.dashboard_forecast(forecast, plan, now)
    assert len(rows) == 15
