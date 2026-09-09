import sys
from pathlib import Path

MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))

from pool_auto_gate import AutoModeGateMixin


class _Base:
    def _manage_pac_auto(self):
        self.manage_delegated += 1

    def _check_pac_start(self, kwargs):
        self.check_delegated += 1


class _GateApp(AutoModeGateMixin, _Base):
    pass


def make_app(state="on", configured=True):
    app = object.__new__(_GateApp)
    app.entity_mode_auto = "input_boolean.pool_auto" if configured else None
    app.gestion_pac_auto = True
    app.handle_pac_auto_start = "timer"
    app.cancelled = 0
    app.manage_delegated = 0
    app.check_delegated = 0
    app.safety_ticks = 0
    app.get_state = lambda entity_id: state
    app._cancel_pac_start = lambda: setattr(app, "cancelled", app.cancelled + 1)
    app.safety_tick = lambda kwargs: setattr(app, "safety_ticks", app.safety_ticks + 1)
    return app


def test_no_auto_mode_entity_keeps_backward_compatible_behavior():
    app = make_app(configured=False)
    assert app.mode_auto_autorise() is True
    app._manage_pac_auto()
    assert app.manage_delegated == 1
    assert app.cancelled == 0


def test_configured_auto_mode_requires_on_state():
    assert make_app(state="on").mode_auto_autorise() is True
    assert make_app(state="off").mode_auto_autorise() is False
    assert make_app(state="unavailable").mode_auto_autorise() is False


def test_auto_mode_off_suspends_normal_pac_policy_without_delegating():
    app = make_app(state="off")
    app._manage_pac_auto()
    assert app.cancelled == 1
    assert app.manage_delegated == 0


def test_auto_mode_on_delegates_to_normal_pac_policy():
    app = make_app(state="on")
    app._manage_pac_auto()
    assert app.cancelled == 0
    assert app.manage_delegated == 1


def test_pending_pac_start_is_aborted_if_auto_mode_is_disabled():
    app = make_app(state="off")
    app._check_pac_start({})
    assert app.handle_pac_auto_start is None
    assert app.cancelled == 1
    assert app.check_delegated == 0


def test_auto_mode_change_re_evaluates_safety_immediately():
    app = make_app(state="off")
    app.change_mode_auto("input_boolean.pool_auto", "state", "on", "off", {})
    assert app.cancelled == 1
    assert app.safety_ticks == 1
