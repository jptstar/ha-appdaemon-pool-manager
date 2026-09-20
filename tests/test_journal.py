import datetime
import sys
from pathlib import Path
from types import SimpleNamespace


MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))

from pool_journal import journal_category, journal_french, publish_journal_event


def test_journal_normalization_stays_out_of_control_policy():
    assert journal_category("PAC circulation en sécurité") == "SÉCURITÉ"
    assert journal_category("plan MPC PREHEAT") == "MPC"
    assert journal_french("end_season -> PREHEAT Heat/Smart") == (
        "fin de saison → préchauffage chauffage Smart"
    )


def test_cover_transition_tokens_are_translated_before_open_substrings():
    assert journal_french("Volet piscine: closed -> opening") == (
        "Volet piscine: fermé → en ouverture"
    )
    assert journal_french("Volet piscine: open -> closing") == (
        "Volet piscine: ouvert → en fermeture"
    )


def test_journal_publication_is_deduplicated_and_bounded():
    writes = []
    controller = SimpleNamespace(
        entity_pool_manager_log="sensor.pool_manager_log",
        pool_manager_log_history_size=10,
        _pool_manager_log_history=[],
        set_state=lambda entity, **payload: writes.append((entity, payload)),
    )
    now = datetime.datetime(2026, 9, 20, 12, 0, 0)

    assert publish_journal_event(controller, "PAC Heat/Smart", now=now) is True
    assert publish_journal_event(controller, "PAC Heat/Smart", now=now) is False
    assert len(writes) == 1
    assert writes[0][0] == "sensor.pool_manager_log"
    assert writes[0][1]["attributes"]["category"] == "PAC"
    assert writes[0][1]["attributes"]["history_limit"] == 10
