"""Heat pump drawing power while shown off, and the wintering mode.

Born from the 2 November 2026 incident: the Pro Elyo Touch started its own
winter antifreeze protection (ambient probe drifting to -9 C while the real air
was ~4 C), drew ~1.4 kW for two hours with `climate` reading `off`, and Pool
Manager never sent a stop because `_pac_off` returns early on state `off`.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "apps" / "pool_manager"))

import check_pac_ghost  # noqa: E402
from pool_heating import (  # noqa: E402
    CHAUFFAGE_HIVERNAGE,
    CHAUFFAGE_MODES,
    chauffage_mode_kind,
)


def test_wintering_mode_is_a_known_selector_option():
    assert CHAUFFAGE_HIVERNAGE in CHAUFFAGE_MODES
    assert chauffage_mode_kind(CHAUFFAGE_HIVERNAGE) == "wintering"
    assert chauffage_mode_kind("Hivernage") == "unknown"


@pytest.mark.parametrize(
    "mode,needs_cycle",
    [(m, c) for m in ("Intelligent", "Hors Gel", "Marche Forcée") for c in (False, True)],
)
def test_pac_running_while_shown_off_is_stopped(mode, needs_cycle):
    rows = [
        r for r in check_pac_ghost.run_recovery()
        if r["mode"] == mode and r["needs_cycle"] == needs_cycle
    ]
    assert rows and all(r["ok"] for r in rows), rows


def test_wintering_never_commands_or_alarms_the_pac():
    rows = check_pac_ghost.run_wintering()
    assert all(r["ok"] for r in rows), rows


def test_legitimate_heating_is_never_taken_for_a_ghost():
    rows = check_pac_ghost.run_no_false_positive()
    assert all(r["ok"] for r in rows), rows


class _Probe:
    """SafetyMixin with just enough stubs to exercise its PAC guards."""

    @staticmethod
    def build(wintering, state, power_active=True, flow_ok=False):
        from pool_safety import SafetyMixin

        class Probe(SafetyMixin):
            def __init__(self):
                self.calls = []
                self.flow_requests = 0
                self._safety_faults = set()
                self.fail_safe_active = True
                self.pac_compressor_started_at = None
                self.pac_flow_fault_since = None
                self.pac_flow_fail_timeout_s = 120
                self.entity_pac_climate = "climate.pac"
                self._chauffage_heat_owned = True
                self.pac_deferred_stop_reason = None

            def pac_hivernage(self):
                return wintering

            def _cancel_pac_start(self):
                pass

            def _pac_state(self):
                return state

            def _pac_power_active(self):
                return power_active

            def _pump_flow_ok(self):
                return flow_ok

            def _ensure_pac_flow(self):
                self.flow_requests += 1

            def _pac_mark_stopped(self):
                pass

            def log(self, *args, **kwargs):
                pass

            def call_service(self, service, **kwargs):
                self.calls.append((service, kwargs))

        return Probe()


def test_wintering_pac_off_sends_nothing_even_when_the_pac_reads_heat():
    probe = _Probe.build(wintering=True, state="heat")
    assert probe._pac_off("mode hors gel", post=False) is True
    assert probe.calls == []


def test_outside_wintering_pac_off_does_stop_a_pac_reading_heat():
    probe = _Probe.build(wintering=False, state="heat")
    assert probe._pac_off("mode hors gel", post=False) is True
    assert [c[1]["hvac_mode"] for c in probe.calls] == ["off"]


def test_wintering_raises_no_flow_alarm_even_if_the_power_sensor_stays_high():
    probe = _Probe.build(wintering=True, state="off", power_active=True, flow_ok=False)
    probe._protect_pac_flow()
    assert probe.flow_requests == 0
    assert "pac_flow" not in probe._safety_faults


def test_outside_wintering_the_same_situation_does_raise_the_flow_alarm():
    probe = _Probe.build(wintering=False, state="off", power_active=True, flow_ok=False)
    probe._protect_pac_flow()
    assert probe.flow_requests >= 1
    assert "pac_flow" in probe._safety_faults
