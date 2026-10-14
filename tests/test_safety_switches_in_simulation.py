"""Safety switches exercised end to end in the offline simulator.

Added after a mutation test showed that breaking the forced-stop switch or the
antifreeze mixing was detected by no test and no simulation campaign.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import check_forced_stop_entity  # noqa: E402
import check_frost_protection  # noqa: E402


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4, 5, 6, 7])
def test_forced_stop_entity_stops_pump_and_pac_in_every_mode(index):
    row = check_forced_stop_entity.check(index)
    assert row["bad_steps"] == 0, row


def test_antifreeze_mixing_runs_in_hors_gel_mode():
    assert check_frost_protection.run_cases(4) == []


@pytest.mark.parametrize("index", range(10))
def test_continuous_freeze_protection_overrides_forced_stop(index):
    # 5 modes x (switch off / on): the circulation must run in every mode,
    # including the Arrêt Forcé selector and the forced-stop switch.
    row = check_forced_stop_entity.check_frost_priority(index)
    assert row["bad_steps"] == 0, row
