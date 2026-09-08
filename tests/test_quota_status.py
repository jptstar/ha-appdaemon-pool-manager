from pathlib import Path
import sys

MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))

from pool_common import build_quota_status, format_duree_hm


def test_format_decimal_hours_as_hours_and_minutes():
    assert format_duree_hm(14.5) == "14 h 30"
    assert format_duree_hm(6.2) == "6 h 12"


def test_quota_status_reports_need_done_remaining_and_decision():
    assert build_quota_status(14.5, 6.2, "Attente surplus solaire") == (
        "BESOIN 14 h 30 | EFFECTUÉ 6 h 12 | RESTANT 8 h 18 | "
        "DÉCISION Attente surplus solaire"
    )


def test_quota_remaining_never_goes_negative():
    status = build_quota_status(10, 12, "Objectif atteint")
    assert "EFFECTUÉ 12 h 00" in status
    assert "RESTANT 0 h 00" in status
