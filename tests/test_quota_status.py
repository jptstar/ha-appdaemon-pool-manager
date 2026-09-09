from pathlib import Path
import sys

MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))

from pool_common import build_quota_status, format_duree_hm, remove_legacy_progress_fragments
from pool_status import compact_detail_status


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


def test_countdown_seconds_are_replaced_by_stable_semantic_states():
    detail = "arrêt dans 536s | 47% | anti-coupure 421s | maintien 97s"
    assert compact_detail_status(detail) == (
        "temporisation arrêt | 47% | anti-coupure | maintien"
    )


def test_start_and_surplus_stability_countdowns_no_longer_churn_detail():
    first = compact_detail_status("attente on 412s | stabilité 127s | surplus 650W")
    second = compact_detail_status("attente on 411s | stabilité 126s | surplus 642W")
    assert first == "temporisation démarrage | attente surplus stable"
    assert second == first


def test_instantaneous_electrical_values_are_removed_from_detail_but_useful_context_stays():
    detail = "quota critique | réseau 862W | PV 798W | pompe 327W réel | 70% | limite 22:00"
    assert compact_detail_status(detail) == "quota critique | 70% | limite 22:00"


def test_duplicate_semantic_timer_labels_are_not_repeated():
    detail = "anti-coupure 200s | anti-coupure 199s | quota différé"
    assert compact_detail_status(detail) == "anti-coupure | quota différé"
