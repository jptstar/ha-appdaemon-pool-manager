import datetime
import importlib.util
import sys
from pathlib import Path


MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))

MPC_PATH = MODULE_DIR / "pool_mpc.py"
spec = importlib.util.spec_from_file_location("pool_mpc", MPC_PATH)
pool_mpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pool_mpc)

PREDICTIVE_PATH = MODULE_DIR / "pool_predictive.py"
pred_spec = importlib.util.spec_from_file_location("pool_predictive_for_mpc", PREDICTIVE_PATH)
pool_predictive = importlib.util.module_from_spec(pred_spec)
pred_spec.loader.exec_module(pool_predictive)


def _forecast(now, specs):
    raw = []
    for index, item in enumerate(specs):
        temperature, condition, low = item[:3]
        raw.append(
            {
                "datetime": datetime.datetime.combine(
                    now.date() + datetime.timedelta(days=index),
                    datetime.time(12, 0),
                ).isoformat(),
                "temperature": temperature,
                "templow": low,
                "condition": condition,
                "precipitation_probability": 0,
                "precipitation": 0,
                "wind_speed": 4,
            }
        )
    return pool_predictive.normalize_daily_forecast(raw)


def _plan(now, forecast, **kwargs):
    defaults = dict(
        now=now,
        water_c=26.0,
        target_c=30.0,
        forecast=forecast,
        base_heating_rate_c_per_h=0.30,
        heating_rate_model={},
        loss_model={},
        cover_state="closed",
        minimum_water_c=22.0,
        floor_delta_c=4.0,
        floor_recharge_c=0.5,
        stop_margin_c=0.2,
        score_min=55.0,
        min_air_c=21.0,
        smart_preset="Smart",
        turbo_preset="Turbo",
        day_hours=8.0,
        today_day_hours_remaining=8.0,
        candidate_day_hours=6.0,
        night_hours=10.0,
        loss_fallback_delta10_c_per_h=0.0,
        daylight_active=True,
        smart_power_fallback_w=1200,
        turbo_power_fallback_w=1900,
        step_h=1.0,
        state_step_c=0.1,
    )
    defaults.update(kwargs)
    return pool_mpc.build_mpc_plan(**defaults)


def test_adaptive_model_confidence_uses_persistent_sample_counts():
    model = pool_mpc.AdaptiveThermalModel(
        base_heating_rate_c_per_h=0.3,
        heating_rate_model={
            "smart": {"20_25": {"rate": 0.4, "count": 8, "power_w": 1000}},
            "turbo": {"20_25": {"rate": 0.6, "count": 5, "power_w": 1600}},
        },
        loss_model={"closed": {"5_10": {"rate": 0.03, "count": 7}}},
    )
    confidence = model.confidence()
    assert confidence["level"] == "adapted"
    assert confidence["heating_samples"] == 13
    assert confidence["loss_samples"] == 7


def test_mpc_waits_for_warmer_more_efficient_day_when_recovery_remains_possible():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (16, "cloudy", 10),
            (18, "cloudy", 11),
            (26, "sunny", 18),
        ],
    )
    learned = {
        "smart": {
            "15_20": {"rate": 0.18, "count": 30, "power_w": 1200},
            "25_30": {"rate": 0.65, "count": 30, "power_w": 1200},
        },
        "turbo": {
            "15_20": {"rate": 0.28, "count": 30, "power_w": 1900},
            "25_30": {"rate": 0.80, "count": 30, "power_w": 1900},
        },
    }
    # ambient_bin uses ge25 for the warm candidate.
    learned["smart"]["ge25"] = learned["smart"].pop("25_30")
    learned["turbo"]["ge25"] = learned["turbo"].pop("25_30")

    plan = _plan(
        now,
        forecast,
        water_c=27.0,
        target_c=30.0,
        heating_rate_model=learned,
    )

    assert plan["planner"] == "MPC"
    assert plan["candidate"]["date"] == now.date() + datetime.timedelta(days=2)
    assert plan["mpc_plan"][0]["heat_hours"] == 0
    assert plan["action"] == "WAIT"
    assert plan["mpc_plan"][-1]["heat_hours"] > 0


def test_mpc_adaptive_floor_can_be_higher_than_absolute_floor():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (18, "cloudy", 12),
            (24, "sunny", 16),
        ],
    )
    learned = {
        "smart": {
            "15_20": {"rate": 0.25, "count": 30, "power_w": 1100},
            "20_25": {"rate": 0.25, "count": 30, "power_w": 1100},
        },
        "turbo": {
            "15_20": {"rate": 0.35, "count": 30, "power_w": 1800},
            "20_25": {"rate": 0.35, "count": 30, "power_w": 1800},
        },
    }
    plan = _plan(
        now,
        forecast,
        water_c=28.0,
        target_c=30.0,
        heating_rate_model=learned,
        today_day_hours_remaining=2.0,
        candidate_day_hours=4.0,
        day_hours=4.0,
    )

    assert plan["candidate"] is not None
    assert plan["adaptive_floor_c"] > 22.0
    assert plan["trajectory_target_c"] >= plan["adaptive_floor_c"]
    assert plan["thermal_margin_c"] == round(
        28.0 - plan["adaptive_floor_c"], 2
    )


def test_mpc_uses_night_only_when_daytime_plan_is_not_feasible():
    now = datetime.datetime(2026, 9, 18, 21, 0)
    forecast = _forecast(
        now,
        [
            (17, "cloudy", 11),
            (24, "sunny", 15),
        ],
    )
    learned = {
        "smart": {
            "15_20": {"rate": 0.25, "count": 30, "power_w": 1100},
            "20_25": {"rate": 0.25, "count": 30, "power_w": 1100},
        },
        "turbo": {
            "15_20": {"rate": 0.40, "count": 30, "power_w": 1800},
            "20_25": {"rate": 0.40, "count": 30, "power_w": 1800},
        },
    }
    plan = _plan(
        now,
        forecast,
        water_c=27.0,
        target_c=30.0,
        heating_rate_model=learned,
        today_day_hours_remaining=0.0,
        candidate_day_hours=1.0,
        night_hours=8.0,
        daylight_active=False,
    )

    assert plan["candidate"] is not None
    assert plan["mpc_night_energy_required"] is True
    assert plan["should_heat"] is True
    assert plan["night_heating"] is True
    assert plan["mpc_plan"][0]["night_heat_hours"] > 0


def test_mpc_energy_estimate_uses_learned_power():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (17, "cloudy", 11),
            (25, "sunny", 17),
        ],
    )
    learned = {
        "smart": {
            "ge25": {"rate": 0.50, "count": 30, "power_w": 800},
            "15_20": {"rate": 0.20, "count": 30, "power_w": 800},
        },
        "turbo": {
            "ge25": {"rate": 0.65, "count": 30, "power_w": 2000},
            "15_20": {"rate": 0.30, "count": 30, "power_w": 2000},
        },
    }
    plan = _plan(
        now,
        forecast,
        water_c=28.0,
        target_c=30.0,
        heating_rate_model=learned,
        candidate_day_hours=5.0,
    )

    assert plan["candidate"] is not None
    assert plan["mpc_energy_kwh"] > 0
    assert any(
        item["preset"] == "Smart" and item["heat_hours"] > 0
        for item in plan["mpc_plan"]
    )


def test_no_swim_window_keeps_existing_floor_policy_under_mpc():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (14, "rainy", 8),
            (15, "rainy", 9),
            (16, "cloudy", 10),
        ],
    )
    plan = _plan(now, forecast, water_c=25.0, target_c=30.0)

    assert plan["planner"] == "MPC"
    assert plan["candidate"] is None
    assert plan["action"] == "WAIT"
    assert plan["should_heat"] is False
    assert plan["adaptive_floor_c"] == 22.0


def test_dashboard_forecast_exposes_exact_mpc_heat_days():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (16, "cloudy", 10),
            (18, "cloudy", 11),
            (26, "sunny", 18),
        ],
    )
    learned = {
        "smart": {
            "15_20": {"rate": 0.18, "count": 30, "power_w": 1200},
            "ge25": {"rate": 0.65, "count": 30, "power_w": 1200},
        },
        "turbo": {
            "15_20": {"rate": 0.28, "count": 30, "power_w": 1900},
            "ge25": {"rate": 0.80, "count": 30, "power_w": 1900},
        },
    }
    plan = _plan(
        now,
        forecast,
        water_c=27.0,
        target_c=30.0,
        heating_rate_model=learned,
    )
    rows = pool_predictive.dashboard_forecast(forecast, plan, now)

    assert rows[0]["mpc_heat_hours"] == 0
    assert rows[-1]["mpc_heat_hours"] > 0
    assert rows[-1]["mpc_preset"] in {"Smart", "Turbo"}
    assert rows[-1]["predicted_water_end"] is not None
