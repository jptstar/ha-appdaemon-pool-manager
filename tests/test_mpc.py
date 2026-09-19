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


def test_weekend_bonus_prefers_marginal_weekend_without_forcing_bad_weather():
    now = datetime.datetime(2026, 9, 18, 8, 0)  # Friday
    forecast = _forecast(
        now,
        [
            (24, "sunny", 16),
            (24, "sunny", 16),
        ],
    )
    for row in forecast:
        row["usage_temperature"] = 24.0
        row["usage_score"] = 50.0
        row["strategic_score"] = 50.0

    opportunities = pool_predictive.find_swim_opportunities(
        forecast,
        now,
        score_min=55.0,
        min_air_c=21.0,
        weekend_bonus=10.0,
    )

    assert [item["date"] for item in opportunities] == [
        now.date() + datetime.timedelta(days=1)
    ]
    assert opportunities[0]["weekend"] is True
    assert opportunities[0]["eligibility_score"] == 60.0


def test_mpc_plans_multiple_bathing_windows_across_full_horizon():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (18, "cloudy", 12),
            (25, "sunny", 17),
            (26, "sunny", 18),
            (16, "rainy", 10),
            (17, "rainy", 11),
            (27, "sunny", 18),
        ],
    )
    usage_scores = [20, 72, 75, 15, 20, 74]
    usage_temps = [18, 25, 26, 16, 17, 27]
    for row, score, temp in zip(forecast, usage_scores, usage_temps):
        row["usage_score"] = float(score)
        row["strategic_score"] = float(score)
        row["usage_temperature"] = float(temp)

    learned = {
        "smart": {
            "15_20": {"rate": 0.16, "count": 30, "power_w": 1200},
            "20_25": {"rate": 0.45, "count": 30, "power_w": 1100},
            "ge25": {"rate": 0.70, "count": 30, "power_w": 1000},
        },
        "turbo": {
            "15_20": {"rate": 0.28, "count": 30, "power_w": 1900},
            "20_25": {"rate": 0.60, "count": 30, "power_w": 1800},
            "ge25": {"rate": 0.90, "count": 30, "power_w": 1700},
        },
    }

    plan = _plan(
        now,
        forecast,
        water_c=30.0,
        target_c=30.0,
        heating_rate_model=learned,
        loss_fallback_delta10_c_per_h=0.03,
    )

    expected_swims = [
        now.date() + datetime.timedelta(days=1),
        now.date() + datetime.timedelta(days=2),
        now.date() + datetime.timedelta(days=5),
    ]
    assert plan["swim_dates"] == expected_swims
    assert len(plan["mpc_plan"]) == len(forecast)
    assert [item["date"] for item in plan["mpc_plan"] if item["swim"]] == expected_swims
    assert plan["mpc_horizon_energy_kwh"] == plan["mpc_energy_kwh"]
    assert all("recoverability_floor" in item for item in plan["mpc_plan"])


def test_mpc_can_coast_through_bad_days_then_reheat_for_later_swim():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (25, "sunny", 17),
            (26, "sunny", 18),
            (15, "rainy", 8),
            (15, "rainy", 8),
            (28, "sunny", 19),
        ],
    )
    usage_scores = [70, 74, 10, 10, 78]
    usage_temps = [25, 26, 15, 15, 28]
    for row, score, temp in zip(forecast, usage_scores, usage_temps):
        row["usage_score"] = float(score)
        row["strategic_score"] = float(score)
        row["usage_temperature"] = float(temp)

    learned = {
        "smart": {
            "10_15": {"rate": 0.10, "count": 30, "power_w": 1250},
            "15_20": {"rate": 0.15, "count": 30, "power_w": 1200},
            "20_25": {"rate": 0.45, "count": 30, "power_w": 1050},
            "ge25": {"rate": 0.75, "count": 30, "power_w": 950},
        },
        "turbo": {
            "10_15": {"rate": 0.18, "count": 30, "power_w": 1950},
            "15_20": {"rate": 0.25, "count": 30, "power_w": 1900},
            "20_25": {"rate": 0.60, "count": 30, "power_w": 1800},
            "ge25": {"rate": 0.95, "count": 30, "power_w": 1700},
        },
    }

    plan = _plan(
        now,
        forecast,
        water_c=30.0,
        target_c=30.0,
        heating_rate_model=learned,
        loss_fallback_delta10_c_per_h=0.025,
        day_hours=8.0,
        candidate_day_hours=6.0,
    )
    path = plan["mpc_plan"]

    assert path[2]["swim"] is False
    assert path[3]["swim"] is False
    assert path[2]["target_date"] == now.date() + datetime.timedelta(days=4)
    assert path[3]["target_date"] == now.date() + datetime.timedelta(days=4)
    assert path[4]["swim"] is True
    assert path[4]["day_end_temperature"] >= 29.8
    assert min(path[2]["end_temperature"], path[3]["end_temperature"]) < 30.0


def test_dashboard_forecast_exposes_multi_horizon_actions_and_targets():
    now = datetime.datetime(2026, 9, 18, 8, 0)
    forecast = _forecast(
        now,
        [
            (24, "sunny", 16),
            (15, "rainy", 8),
            (26, "sunny", 18),
        ],
    )
    for row, score, temp in zip(forecast, [70, 10, 75], [24, 15, 26]):
        row["usage_score"] = float(score)
        row["strategic_score"] = float(score)
        row["usage_temperature"] = float(temp)

    plan = _plan(
        now,
        forecast,
        water_c=30.0,
        target_c=30.0,
        loss_fallback_delta10_c_per_h=0.02,
    )
    rows = pool_predictive.dashboard_forecast(forecast, plan, now)

    assert rows[0]["swim"] is True
    assert rows[2]["swim"] is True
    assert rows[1]["mpc_target_date"] == (
        now.date() + datetime.timedelta(days=2)
    ).isoformat()
    assert rows[1]["recoverability_floor"] is not None
    assert rows[0]["primary_swim"] is True
