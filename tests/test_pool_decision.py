import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "apps" / "pool_manager"))
from pool_decision import DecisionSupport
from pool_control import forecast_filtration_target


class App(DecisionSupport):
    def __init__(self):
        self.args = {"pool_notify_service": "notify.mobile_app_test"}
        self.entity_chauffage = "input_select.pool_heating"
        self.chauffage_preset_smart = "Smart"
        self.chauffage_preset_turbo = "Turbo"
        self.mode = "Fin de saison • Smart"
        self.stopped = False
        self.daylight = True
        self.calls = []
        self.states = []
        self.listen_event = lambda *args: None
        self._initialize_pool_decisions()

    def get_state(self, entity):
        return self.mode

    def call_service(self, service, **kwargs):
        self.calls.append((service, kwargs))

    def set_state(self, *args, **kwargs):
        self.states.append(kwargs)

    def arret_force_actif(self):
        return self.stopped

    def _predictive_daylight_active(self):
        return self.daylight

    def traitement(self, kwargs):
        pass


def request(app):
    return app._apply_pool_decision(
        {"should_heat": True, "preset": "Turbo", "heat_target_c": 31}, 27, 31
    )


def action(app, choice):
    return next(a for a, c in app._pool_pending["actions"].items() if c == choice)


def test_no_reply_is_smart_and_single_notice():
    app = App()
    assert request(app)["preset"] == "Smart"
    request(app)
    assert len(app.calls) == 1
    assert len(app.calls[0][1]["data"]["actions"]) == 3


def test_duplicate_turbo_cannot_extend_timer():
    app = App()
    request(app)
    data = {"action": action(app, "turbo")}
    app._pool_notification_action(None, data, {})
    app._pool_notification_action(None, data, {})
    selects = [c for c in app.calls if c[0] == "input_select/select_option"]
    assert len(selects) == 1
    assert selects[0][1]["option"] == "Turbo • 1 h"


def test_expired_foreign_and_forced_stop_actions_do_not_start_turbo():
    for condition in ("expired", "foreign", "stop", "mode"):
        app = App()
        request(app)
        data = {"action": action(app, "turbo")}
        if condition == "expired":
            app._pool_pending["expires"] = datetime.datetime.now() - datetime.timedelta(
                seconds=1
            )
        elif condition == "foreign":
            data["action"] = "POOL_FOREIGN_turbo"
        elif condition == "mode":
            app.mode = "Désactivé"
        else:
            app.stopped = True
        app._pool_notification_action(None, data, {})
        assert not any(s == "input_select/select_option" for s, _ in app.calls)


def test_skip_applies_until_day_boundary_only():
    app = App()
    request(app)
    app._pool_notification_action(None, {"action": action(app, "skip")}, {})
    plan = {"should_heat": True, "preset": "Smart", "heat_target_c": 30}
    assert not app._apply_pool_decision(plan, 27, 31)["should_heat"]
    tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
    assert app._apply_pool_decision(plan, 27, 31, now=tomorrow)["should_heat"]


def test_economy_plan_is_recomputed_not_just_relabelled():
    app = App()
    plan = {"should_heat": True, "preset": "Turbo", "mpc_energy_kwh": 9}
    eco = {"should_heat": True, "preset": "Smart", "mpc_energy_kwh": 3}
    assert (
        app._apply_pool_decision(plan, 27, 31, economy_plan=lambda: eco)[
            "mpc_energy_kwh"
        ]
        == 3
    )
    app.daylight = False
    assert not app._apply_pool_decision(plan, 27, 31)["should_heat"]


def test_persistent_fallback_and_recovered_plan_invalidates_buttons():
    app = App()
    app.args = {}
    request(app)
    assert app.calls[0][0] == "persistent_notification/create"
    stale = action(app, "turbo")
    app._apply_pool_decision({"should_heat": False}, 30, 31)
    app._pool_notification_action(None, {"action": stale}, {})
    assert not any(s == "input_select/select_option" for s, _ in app.calls)


def test_quota_forecasts_today_and_rejects_stale_future_or_stopped_plans():
    now = datetime.datetime(2026, 9, 25, 10)
    plan = {
        "should_heat": True,
        "heat_target_c": 29,
        "mpc_plan": [
            {"date": now.date(), "day_heat_hours": 5, "day_end_temperature": 29}
        ],
    }
    assert forecast_filtration_target(12, 24, 1, False, plan, now, now) == 14.5
    assert (
        forecast_filtration_target(
            12, 24, 1, False, plan, now - datetime.timedelta(minutes=6), now
        )
        == 12
    )
    plan["mpc_plan"][0]["date"] += datetime.timedelta(days=1)
    assert forecast_filtration_target(12, 24, 1, False, plan, now, now) == 12
    plan["should_heat"] = False
    assert forecast_filtration_target(12, 24, 1, False, plan, now, now) == 12
