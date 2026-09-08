from pathlib import Path
import sys

MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))

from pool_common import build_quota_status, format_duree_hm, remove_legacy_progress_fragments


def test_format_decimal_hours_as_hours_and_minutes():
    assert format_duree_hm(14.5) == "14 h 30"
    assert format_duree_hm(6.2) == "6 h 12"


def test_quota_status_reports_need_done_and_remaining():
    assert build_quota_status(14.5, 6.2) == (
        "Besoin 14 h 30 | Effectué 6 h 12 | Restant 8 h 18"
    )


def test_quota_remaining_never_goes_negative():
    status = build_quota_status(10, 12)
    assert "Effectué 12 h 00" in status
    assert "Restant 0 h 00" in status


def test_legacy_decimal_progress_is_removed_from_detail():
    detail = "47% | 8.0m3/h | 6.2/14.5h | limite 22:00"
    assert remove_legacy_progress_fragments(detail) == "47% | 8.0m3/h | limite 22:00"


def test_non_progress_detail_is_preserved():
    detail = "démarrage | 47% | fin 17:30"
    assert remove_legacy_progress_fragments(detail) == detail
