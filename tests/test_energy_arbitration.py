import sys
from pathlib import Path

MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))

from pool_strategy import StrategyMixin


class FakeStrategy(StrategyMixin):
    garantir_quota_journalier = True
    vitesse_min_filtration_utile = 47
    vitesse_reference_filtration = 70
    vitesse_min_garantie_quota = 47
    vitesse_max_garantie_quota = 100
    vitesse_pac_prioritaire = 47
    vitesse_min_pac_active = 47
    pac_prioritaire_absolue = True
    heure_limite_quota_journalier = "22:00:00"
    heure_debut_solaire = "09:00:00"
    heure_fin_solaire = "18:00:00"
    pac_import_max_jour_w = 300

    def __init__(self, remaining_h=7.0, in_solar_window=True):
        self.remaining_h = remaining_h
        self.in_solar_window = in_solar_window
        self.debut_manque_soleil = None
        self.messages = None
        self.debug = None
        self.speed = None
        self.started = None

    def temps_restant_avant_limite_quota_h(self):
        return self.remaining_h

    def est_dans_plage(self, debut, fin):
        return self.in_solar_window

    def reset_stabilite_surplus(self):
        pass

    def reset_pid(self):
        pass

    def start_pump_with_delayed_speed(self, speed, delay_s=2, context=""):
        self.started = (speed, context)

    def set_pump_percentage(self, speed):
        self.speed = speed
        return speed

    def set_messages(self, short, detail=""):
        self.messages = (short, detail)

    def set_debug_w(self, value):
        self.debug = value

    def format_texte_solaire_debug(self, speed, surplus_net=None, reseau_net=None, pv_power=None):
        self.debug = (speed, surplus_net, reseau_net, pv_power)

    def split_message_solaire(self, state, speed, filtre, objectif, extra=""):
        return state, f"{speed}% | {extra}".strip(" |")


def test_impossible_quota_does_not_force_100_percent():
    strategy = FakeStrategy(remaining_h=7.17)
    state = strategy.etat_garantie_quota(13.15, 23.53)

    assert state["recuperable"] is False
    assert state["critique"] is False
    assert state["vitesse"] == 100
    assert state["vitesse_requise"] > 100


def test_recoverable_quota_only_becomes_hard_priority_near_limit():
    strategy = FakeStrategy(remaining_h=4.0)

    comfortable = strategy.etat_garantie_quota(10.0, 14.0)
    critical = strategy.etat_garantie_quota(8.75, 14.0)

    assert comfortable["recuperable"] is True
    assert comfortable["critique"] is False
    assert critical["recuperable"] is True
    assert critical["critique"] is True


def test_pac_high_grid_import_holds_minimum_speed_when_quota_is_impossible():
    strategy = FakeStrategy(remaining_h=7.17, in_solar_window=True)

    handled = strategy.appliquer_priorite_pac_ou_quota(
        filtre_temps_eq=13.15,
        objectif_temps_eq=23.53,
        pompe_on=True,
        pac_besoin=True,
        pac_chauffe=True,
        etat_pac="turbo",
        surplus_net=0,
        reseau_net=2721,
    )

    assert handled is True
    assert strategy.speed == 47
    assert strategy.messages[0] == "PAC éco réseau"
    assert "quota impossible" in strategy.messages[1]


def test_pac_with_export_returns_control_to_solar_pid():
    strategy = FakeStrategy(remaining_h=7.17, in_solar_window=True)

    handled = strategy.appliquer_priorite_pac_ou_quota(
        filtre_temps_eq=13.15,
        objectif_temps_eq=23.53,
        pompe_on=True,
        pac_besoin=True,
        pac_chauffe=True,
        etat_pac="turbo",
        surplus_net=985,
        reseau_net=-1035,
    )

    assert handled is False
    assert strategy.speed is None


def test_pac_still_starts_at_minimum_before_heat_pump_can_run():
    strategy = FakeStrategy(remaining_h=7.17, in_solar_window=True)

    handled = strategy.appliquer_priorite_pac_ou_quota(
        filtre_temps_eq=13.15,
        objectif_temps_eq=23.53,
        pompe_on=False,
        pac_besoin=True,
        pac_chauffe=False,
        etat_pac="off",
        surplus_net=985,
        reseau_net=-1035,
    )

    assert handled is True
    assert strategy.started == (47, "pac_prioritaire")
