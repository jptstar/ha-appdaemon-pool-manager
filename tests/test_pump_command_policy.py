import datetime
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))

from pool_common import TAB_MODE
from pool_control import ControlMixin
from pool_devices import DevicesMixin


class FakeDevices(DevicesMixin):
    args = {
        "cde_pompe": "fan.pool",
        "fan_variateur_pompe": "fan.pool",
        "mode_de_fonctionnement": "input_select.pool_mode",
    }
    delta_vitesse_min = 5
    tempo_changement_vitesse = 30
    pas_vitesse_max = 10
    entity_pompe_local_panel_assist = "switch.local_assist"

    def __init__(self, current=50, last_command=70, mode=TAB_MODE[1], assist="on"):
        self.current = current
        self.derniere_vitesse_commande = last_command
        self.last_changement_vitesse = datetime.datetime.now() - datetime.timedelta(seconds=60)
        self.mode = mode
        self.assist = assist
        self.calls = []
        self.handle_apply_speed = None
        self.pending_start_context = None
        self.start_sequence_active = False
        self.start_sequence_until = None
        self.handle_delayed_stop = None
        self.stop_sequence_active = False
        self.stop_sequence_until = None
        self.pending_stop_reason = None
        self.chauffage_predictif_measurement_active = False
        self.chauffage_predictif_mesure_vitesse_pct = 70
        self.consigne_electrolyseur_arret = 0.0

    def get_state(self, entity_id, attribute=None):
        if entity_id == "fan.pool" and attribute == "percentage":
            return self.current
        if entity_id == "fan.pool":
            return "on"
        if entity_id == "input_select.pool_mode":
            return self.mode
        if entity_id == "switch.local_assist":
            return self.assist
        return None

    def call_service(self, service, **kwargs):
        self.calls.append((service, kwargs))

    def maj_electrolyseur(self):
        pass

    def log(self, *args, **kwargs):
        pass

    def arret_force_actif(self):
        return False


def test_automatic_control_uses_physical_speed_again():
    devices = FakeDevices(current=50, last_command=70, mode=TAB_MODE[1])

    result = devices.set_pump_percentage(70)

    assert result == 60
    assert devices.calls == [
        ("fan/set_percentage", {"entity_id": "fan.pool", "percentage": 60})
    ]


def test_unchanged_physical_speed_is_not_rewritten():
    devices = FakeDevices(current=70, last_command=70, mode=TAB_MODE[1])

    result = devices.set_pump_percentage(70)

    assert result == 70
    assert devices.calls == []


def test_temperature_and_forced_modes_enable_local_assist():
    for mode in (TAB_MODE[0], TAB_MODE[3]):
        devices = FakeDevices(mode=mode, assist="off")
        devices.sync_local_panel_policy(mode)
        assert devices.calls == [
            ("switch/turn_on", {"entity_id": "switch.local_assist"})
        ]


def test_intelligent_and_safety_modes_disable_local_assist():
    for mode in (TAB_MODE[1], TAB_MODE[2], TAB_MODE[4]):
        devices = FakeDevices(mode=mode, assist="on")
        devices.sync_local_panel_policy(mode)
        assert devices.calls == [
            ("switch/turn_off", {"entity_id": "switch.local_assist"})
        ]


class FakeTemperatureControl(ControlMixin):
    """Pump already running inside the Temperature-mode filtration window."""

    def __init__(self):
        self.args = {
            "mode_de_fonctionnement": "input_select.mode",
            "temperature_eau": "sensor.water",
            "mem_temp": "input_number.mem",
            "coef": "input_number.coef",
            "h_pivot": "input_datetime.pivot",
            "mode_calcul": "input_boolean.abaque",
            "duree_filtration_ete": "input_number.duree",
            "tempo_eau": "input_number.tempo",
        }
        self.fin_tempo = 1
        self.lock_text = False
        self.temps_filtration_equivalent_jour = 2.0
        self.stabilisation_active = False
        self.brassage_en_cours = False
        self.mode_speed_initialized = False
        self.vitesse_mode_temperature = 70
        self.derniere_vitesse_commande = None
        self.chauffage_predictif_measurement_active = False
        self.chauffage_predictif_last_plan = None
        self.chauffage_predictif_last_plan_at = None
        self.speed_commands = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None

    def get_state(self, entity, **kwargs):
        return {"input_select.mode": TAB_MODE[0], "input_datetime.pivot": "13:00:00"}.get(entity, "off")

    def get_float_state(self, entity, default=0.0):
        return {"input_number.coef": 100.0}.get(entity, 26.0)

    def td_now(self):
        return datetime.timedelta(hours=13)

    def arret_force_actif(self):
        return False

    def start_sequence_is_running(self):
        return False

    def stop_sequence_is_running(self):
        return False

    def est_nuit(self, now):
        return False

    def pompe_est_on(self):
        return True

    def get_fan_percentage(self):
        return 55

    def set_pump_percentage(self, value, force=False):
        self.speed_commands.append((value, force))
        return value

    def debit_pompe(self, value):
        return 10.0


def test_temperature_path_only_forces_initial_speed_once():
    app = FakeTemperatureControl()

    app._traitement_filtration({})
    app._traitement_filtration({})

    assert app.mode_speed_initialized is True
    # The reference speed is forced once; later loops keep the live speed.
    assert app.speed_commands == [(70, True)]


def test_predictive_measurement_preserves_speed_above_reference():
    devices = FakeDevices(current=100, last_command=100, mode=TAB_MODE[1])
    devices.chauffage_predictif_measurement_active = True
    devices.chauffage_predictif_mesure_vitesse_pct = 70

    result = devices.set_pump_percentage(100)

    assert result == 100
    assert devices.calls == []


def test_predictive_measurement_can_force_reference_minimum_from_below():
    devices = FakeDevices(current=50, last_command=50, mode=TAB_MODE[1])
    devices.chauffage_predictif_measurement_active = True
    devices.chauffage_predictif_mesure_vitesse_pct = 70

    result = devices.set_pump_percentage(70, force=True)

    assert result == 70
    assert devices.calls == [
        ("fan/set_percentage", {"entity_id": "fan.pool", "percentage": 70})
    ]


def test_predictive_measurement_blocks_normal_drop_below_reference():
    devices = FakeDevices(current=70, last_command=70, mode=TAB_MODE[1])
    devices.chauffage_predictif_measurement_active = True
    devices.chauffage_predictif_mesure_vitesse_pct = 70

    result = devices.set_pump_percentage(47)

    assert result == 70
    assert devices.calls == []



def test_active_certified_measurement_blocks_normal_pump_stop():
    devices = FakeDevices(current=70, last_command=70, mode=TAB_MODE[1])
    devices.chauffage_predictif_measurement_active = True
    devices.cancel_timer = lambda *args, **kwargs: None
    devices.set_consigne_electrolyseur = lambda *args, **kwargs: None
    devices.set_debug_w = lambda *args, **kwargs: None

    stopped = devices.turn_off_pompe_mem(
        reason="consommation maison trop élevée"
    )

    assert stopped is False
    assert devices.calls == []


def test_forced_stop_still_overrides_active_measurement():
    devices = FakeDevices(current=70, last_command=70, mode=TAB_MODE[1])
    devices.chauffage_predictif_measurement_active = True
    devices.cancel_timer = lambda *args, **kwargs: None
    devices.set_consigne_electrolyseur = lambda *args, **kwargs: None
    devices.set_debug_w = lambda *args, **kwargs: None
    devices.mode_speed_initialized = True
    devices.log = lambda *args, **kwargs: None

    stopped = devices.turn_off_pompe_mem(
        force=True,
        reason="arrêt de sécurité",
    )

    assert stopped is True
    assert devices.calls == [
        ("fan/turn_off", {"entity_id": "fan.pool"})
    ]


def test_temperature_window_stop_cannot_interrupt_pac_demand():
    devices = FakeDevices(current=47, mode=TAB_MODE[0])
    devices.cancel_timer = lambda *args, **kwargs: None
    devices.pac_besoin_chauffe = lambda: True
    devices._ensure_pac_flow = lambda: devices.calls.append(('ensure_flow', {}))
    assert devices.turn_off_pompe_mem(reason='hors plage température') is False
    assert devices.calls == [('ensure_flow', {})]


def test_delayed_stop_rechecks_pac_demand_before_switching_pump_off():
    devices = FakeDevices(current=47)
    devices.pac_besoin_chauffe = lambda: True
    devices._ensure_pac_flow = lambda: devices.calls.append(('ensure_flow', {}))
    devices.apply_pending_stop_after_electrolyseur({})
    assert devices.calls == [('ensure_flow', {})]
    assert not devices.stop_sequence_active
