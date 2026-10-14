"""Release-blocking closed-loop contracts; never connects to Home Assistant.

Each run uses a fresh process so AppDaemon mocks and the virtual clock cannot
leak between tests. These are synthetic scenarios, not a calibrated CSV replay.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[1]


def run_offline(case, hours):
    script = """
import json, sys
import simulate_pool as sim
case = json.loads(sys.argv[1])
observed = []
class ObservingBackend(sim.Backend):
    def advance(self, until):
        super().advance(until)
        if self.clock.now.date().isoformat() == case.get('start_time', '2026-10-02')[:10]:
            observed.append({
                'time': self.clock.now.isoformat(),
                'performed_h': self.states['input_number.pool_equivalent_filtration_today']['state'],
                'target_h': self.states['input_number.pool_daily_filtration_target']['state'],
            })
sim.Backend = ObservingBackend
result = sim.simulate(case, hours=int(sys.argv[2]), step_s=300, trace=True)
result['last_first_day_quota'] = observed[-1] if observed else None
print(json.dumps(result))
"""
    run = subprocess.run(
        [sys.executable, "-c", script, json.dumps(case), str(hours)],
        cwd=ROOT / "tools", capture_output=True, text=True, timeout=90,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)


def autumn_case():
    return dict(
        weather="sun", water=23.0, cover="closed", solar="clouds",
        load=500.0, heating="Désactivé", choice="eco", mode="Intelligent",
        fault="none", start_time="2026-10-14T08:00:00",
    )


@pytest.mark.parametrize("solar", ["none", "clouds", "strong"])
def test_daily_energy_accounting_matches_closed_loop_trace(solar):
    case = autumn_case()
    case["solar"] = solar
    result = run_offline(case, 24)
    assert result["violations"] == []
    rows = result["trace"]
    assert len(rows) == 24 * 12
    assert all(row["pac_w"] < 100 for row in rows)
    pump = sum(
        (180 + 1100 * (row["speed_pct"] / 100) ** 3)
        if row["speed_pct"] else 2 for row in rows
    ) * 300 / 3600000
    # Reported energy is rounded to 0.001 kWh, not silently double-counted.
    assert result["energy"]["pump_kwh"] == pytest.approx(pump, abs=0.00051)
    assert result["energy"]["pac_kwh"] == pytest.approx(
        sum(row["pac_w"] for row in rows) * 300 / 3600000, abs=0.00051,
    )
    quota = result["last_first_day_quota"]
    assert quota is not None
    assert float(quota["performed_h"]) + 0.1 >= float(quota["target_h"])
    if solar == "clouds":
        starts = sum(
            row["speed_pct"] > 0 and (index == 0 or rows[index - 1]["speed_pct"] == 0)
            for index, row in enumerate(rows)
        )
        # Same deterministic case used to start sixteen times. The improvement
        # must not be obtained by giving up the daily quota checked above.
        assert starts <= 2


def test_season_start_to_unmanaged_auto_never_loses_pac_circulation():
    """Minimal historical failing family pac-filtration-energy-1036.

    Retain the original 48-hour duration: a short happy-path replay would miss
    the transfer back to Automatique and the subsequent pump stop.
    """
    case = dict(
        weather="sun", water=29.0, cover="closed", solar="none", load=500.0,
        heating="Début de saison • Smart", choice="eco", mode="Intelligent",
        fault="none",
    )
    result = run_offline(case, 48)
    assert result["violations"] == [], result["flow_violation_samples"]
