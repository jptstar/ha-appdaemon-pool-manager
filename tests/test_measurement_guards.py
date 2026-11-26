"""Guards around the high-speed water-temperature calibration.

Reproduces the 2026-11-26 incident: during the antifreeze mode, with heating
closed for the season, a calibration armed at 10:36 held the pump at 70 %
(about 520 W) for five hours, on a red tariff day, because nothing ticked its
timeout.
"""
import datetime

from test_predictive_runtime_learning import _make_runtime

T0 = datetime.datetime(2026, 11, 26, 10, 36)


def _armed(tmp_path, mode="Intelligent", tariff="HCJB"):
    app = _make_runtime(tmp_path)
    states = {"mode": mode, "tariff": tariff}
    app.args["mode_de_fonctionnement"] = "input_select.mode"
    app.tarif_jour_rouge_entity = "sensor.tarif"
    app.tarif_jour_rouge_etat = "HPJR"
    app.get_state = lambda entity, attribute=None: {
        "input_select.mode": states["mode"],
        "sensor.tarif": states["tariff"],
        "input_number.pool_water_circulation_delay": 900,
    }.get(entity)
    app._seasonal_heating_blocked = lambda: False
    app.traitement = lambda kwargs=None: None
    app.states = states
    app.last_pompe_on = T0
    app.last_pompe_off = T0 - datetime.timedelta(minutes=29)
    app.speed = 47
    return app


def test_normal_conditions_still_arm_a_calibration(tmp_path):
    app = _armed(tmp_path)
    app._update_certified_measurement(T0)
    assert app.chauffage_predictif_measurement_active


def test_red_day_peak_period_forbids_any_calibration(tmp_path):
    app = _armed(tmp_path, tariff="HPJR")
    app._update_certified_measurement(T0)
    assert not app.chauffage_predictif_measurement_active
    assert app.speed == 47  # never raised to 70 %


def test_red_day_rule_follows_the_tariff_sensor_not_the_clock(tmp_path):
    # Other tariff periods, including the off-peak hours of a red day,
    # must not block the calibration.
    for tariff in ("HCJR", "HPJB", "HCJW", "unknown", None):
        app = _armed(tmp_path, tariff=tariff)
        app._update_certified_measurement(T0)
        assert app.chauffage_predictif_measurement_active, tariff


def test_antifreeze_mode_forbids_calibration(tmp_path):
    app = _armed(tmp_path, mode="Hors Gel")
    app._update_certified_measurement(T0)
    assert not app.chauffage_predictif_measurement_active
    assert app.speed == 47


def test_seasonal_heating_stop_forbids_calibration(tmp_path):
    app = _armed(tmp_path)
    app._seasonal_heating_blocked = lambda: True
    app._update_certified_measurement(T0)
    assert not app.chauffage_predictif_measurement_active


def test_calibration_armed_earlier_is_released_when_red_day_starts(tmp_path):
    app = _armed(tmp_path)
    app._update_certified_measurement(T0)
    assert app.chauffage_predictif_measurement_active
    app.states["tariff"] = "HPJR"
    app._update_certified_measurement(T0 + datetime.timedelta(minutes=3))
    assert not app.chauffage_predictif_measurement_active


def test_request_path_also_honours_the_guards(tmp_path):
    app = _armed(tmp_path, tariff="HPJR")
    app.arret_force_actif = lambda: False
    app.pompe_est_on = lambda: True
    assert app._request_predictive_measurement("decision") is False
    assert not app.chauffage_predictif_measurement_active


def test_calibration_is_cut_after_the_hard_ceiling(tmp_path):
    app = _armed(tmp_path)
    app._update_certified_measurement(T0)
    assert app.chauffage_predictif_measurement_active
    # The water probe never stabilises: the old code waited for an event.
    app.water = 18.0
    for minute in range(1, 40):
        app.water += 1.0  # always moving
        app._update_certified_measurement(T0 + datetime.timedelta(minutes=minute))
        if not app.chauffage_predictif_measurement_active:
            break
    assert not app.chauffage_predictif_measurement_active
    assert minute <= 31


def test_no_bouncing_between_normal_and_calibration_speed(tmp_path):
    """After a cut, the next attempt is hours away, not 15 minutes."""
    app = _armed(tmp_path)
    app._update_certified_measurement(T0)
    cut_at = T0 + datetime.timedelta(minutes=31)
    app._update_certified_measurement(cut_at)
    assert not app.chauffage_predictif_measurement_active
    # Every later pump start would normally ask for a new calibration.
    for minutes in (16, 45, 120, 300, 359):
        app.last_pompe_on = cut_at + datetime.timedelta(minutes=minutes)
        app._update_certified_measurement(app.last_pompe_on)
        assert not app.chauffage_predictif_measurement_active, minutes
    later = cut_at + datetime.timedelta(hours=6, minutes=1)
    app.last_pompe_on = later
    app._update_certified_measurement(later)
    assert app.chauffage_predictif_measurement_active


def test_housekeeping_tick_enforces_the_ceiling_without_heating_logic(tmp_path):
    """The 30 s housekeeping loop must drive the engine when nothing else does."""
    import pool_runtime_stability as stability

    app = _armed(tmp_path)
    app.last_pompe_on = datetime.datetime.now() - datetime.timedelta(hours=5)
    app.last_pompe_off = app.last_pompe_on - datetime.timedelta(minutes=29)
    long_ago = datetime.datetime.now() - datetime.timedelta(hours=5)
    app._update_certified_measurement(long_ago)
    assert app.chauffage_predictif_measurement_active
    # Field situation: only the housekeeping tick runs, five hours later.
    app._check_etats_speciaux_core = lambda kwargs: None
    app._maintain_remote_pump_authority = lambda: None
    stability.RuntimeStabilityMixin.check_etats_speciaux(app, {})
    assert not app.chauffage_predictif_measurement_active
    assert app.chauffage_predictif_measurement_retry_s_active == 21600


# ----------------------------- option parsing -----------------------------

def _loaded(tmp_path, **args):
    app = _make_runtime(tmp_path)
    app.args = dict(args)
    app._load_measurement_guard_args()
    return app


def test_guard_options_defaults(tmp_path):
    app = _loaded(tmp_path)
    assert app.chauffage_predictif_mesure_duree_max_s == 1800
    assert app.chauffage_predictif_mesure_retry_apres_plafond_s == 21600
    assert app.chauffage_predictif_measurement_retry_s_active == 900
    assert app.tarif_jour_rouge_entity is None
    assert app.tarif_jour_rouge_etat == "HPJR"


def test_guard_options_are_read_from_the_configuration(tmp_path):
    app = _loaded(
        tmp_path,
        chauffage_predictif_mesure_duree_max_s="2400",
        chauffage_predictif_mesure_retry_apres_plafond_s=7200,
        tarif_jour_rouge_entity="sensor.teleinfo_ptec",
        tarif_jour_rouge_etat="HPJR",
    )
    assert app.chauffage_predictif_mesure_duree_max_s == 2400
    assert app.chauffage_predictif_mesure_retry_apres_plafond_s == 7200
    assert app.tarif_jour_rouge_entity == "sensor.teleinfo_ptec"


def test_guard_options_cannot_be_set_dangerously_low(tmp_path):
    app = _loaded(
        tmp_path,
        chauffage_predictif_mesure_duree_max_s=1,
        chauffage_predictif_mesure_retry_apres_plafond_s=1,
    )
    # A ceiling below the circulation time would make every calibration fail;
    # a cool-down under 30 min would allow the speed to bounce.
    assert app.chauffage_predictif_mesure_duree_max_s == 600
    assert app.chauffage_predictif_mesure_retry_apres_plafond_s == 1800


def test_custom_tariff_state_is_honoured(tmp_path):
    app = _armed(tmp_path, tariff="ROUGE")
    app.tarif_jour_rouge_etat = "ROUGE"
    app._update_certified_measurement(T0)
    assert not app.chauffage_predictif_measurement_active


def test_missing_tariff_entity_disables_only_the_red_day_rule(tmp_path):
    app = _armed(tmp_path, tariff="HPJR")
    app.tarif_jour_rouge_entity = None
    app._update_certified_measurement(T0)
    assert app.chauffage_predictif_measurement_active
    # ... while the other protections stay active.
    app2 = _armed(tmp_path, mode="Hors Gel", tariff="HPJR")
    app2.tarif_jour_rouge_entity = None
    app2._update_certified_measurement(T0)
    assert not app2.chauffage_predictif_measurement_active


def test_unreadable_tariff_sensor_does_not_block_or_crash(tmp_path):
    app = _armed(tmp_path)

    def broken(entity, attribute=None):
        if entity == "sensor.tarif":
            raise RuntimeError("sensor offline")
        return {"input_select.mode": "Intelligent"}.get(entity)

    app.get_state = broken
    app._update_certified_measurement(T0)
    assert app.chauffage_predictif_measurement_active


# ----------------------------- return values -----------------------------

def test_forbidden_calibration_reports_false_and_releases_the_pump(tmp_path):
    app = _armed(tmp_path)
    app._update_certified_measurement(T0)
    assert app.chauffage_predictif_measurement_active
    app.states["tariff"] = "HPJR"
    released = []
    app.traitement = lambda kwargs=None: released.append(True)
    assert app._update_certified_measurement(T0 + datetime.timedelta(minutes=1)) is False
    assert released, "speed authority must be handed back to the normal strategy"


# ----------------------------- timeouts -----------------------------

def test_ceiling_uses_the_start_time_when_no_request_time_exists(tmp_path):
    app = _armed(tmp_path)
    app._update_certified_measurement(T0)
    app.chauffage_predictif_measurement_requested_at = None
    app.chauffage_predictif_measurement_started_at = T0
    app.water = 18.0
    assert app._update_certified_measurement(T0 + datetime.timedelta(minutes=31)) is False
    assert not app.chauffage_predictif_measurement_active
    assert app.chauffage_predictif_measurement_retry_s_active == 21600


def test_ceiling_not_reached_keeps_the_calibration_running(tmp_path):
    app = _armed(tmp_path)
    app._update_certified_measurement(T0)
    app.water = 18.0
    app._update_certified_measurement(T0 + datetime.timedelta(minutes=20))
    assert app.chauffage_predictif_measurement_active
    assert app.chauffage_predictif_measurement_failed_at is None


def test_ordinary_timeouts_keep_the_short_retry_delay(tmp_path):
    """Only the ceiling imposes the long cool-down."""
    app = _armed(tmp_path)
    now = T0 + datetime.timedelta(minutes=5)
    app._abort_measurement_timeout(now, "sonde indisponible")
    assert app.chauffage_predictif_measurement_retry_s_active == 900
    assert app._measurement_retry_blocked(now + datetime.timedelta(minutes=14))
    assert not app._measurement_retry_blocked(now + datetime.timedelta(minutes=16))
    app._abort_measurement_timeout(now, "plafond", long_retry=True)
    assert app._measurement_retry_blocked(now + datetime.timedelta(hours=5, minutes=59))
    assert not app._measurement_retry_blocked(now + datetime.timedelta(hours=6, minutes=1))


def test_a_successful_certification_clears_the_cool_down(tmp_path):
    app = _armed(tmp_path)
    app._abort_measurement_timeout(T0, "plafond", long_retry=True)
    assert app._measurement_retry_blocked(T0 + datetime.timedelta(minutes=30))
    app._register_certified_measurement(T0 + datetime.timedelta(hours=7), 9.8)
    assert not app._measurement_retry_blocked(T0 + datetime.timedelta(hours=7))


# ----------------------------- housekeeping tick -----------------------------

def test_housekeeping_tick_leaves_an_idle_engine_alone(tmp_path):
    import pool_runtime_stability as stability

    app = _armed(tmp_path)
    calls = []
    app._update_certified_measurement = lambda *a, **k: calls.append(1)
    app._check_etats_speciaux_core = lambda kwargs: None
    app._maintain_remote_pump_authority = lambda: None
    app.chauffage_predictif_measurement_active = False
    stability.RuntimeStabilityMixin.check_etats_speciaux(app, {})
    del app.chauffage_predictif_measurement_active
    stability.RuntimeStabilityMixin.check_etats_speciaux(app, {})
    assert calls == []


def test_housekeeping_tick_survives_an_engine_error(tmp_path):
    import pool_runtime_stability as stability

    app = _armed(tmp_path)
    app.chauffage_predictif_measurement_active = True
    app._check_etats_speciaux_core = lambda kwargs: None
    done = []
    app._maintain_remote_pump_authority = lambda: done.append(1)

    def boom(*a, **k):
        raise RuntimeError("engine failure")

    app._update_certified_measurement = boom
    stability.RuntimeStabilityMixin.check_etats_speciaux(app, {})  # must not raise
    assert done == [1]


# ----------------------------- wiring -----------------------------

def test_initialisation_loads_the_guard_options():
    """The options must be read when the app starts, not only in the tests."""
    import inspect

    source = inspect.getsource(
        __import__("pool_predictive_runtime").PredictiveHeatingSupport._initialize_predictive_heating
    )
    assert "self._load_measurement_guard_args()" in source


def test_tariff_state_defaults_to_the_peak_red_day_code(tmp_path):
    app = _armed(tmp_path, tariff="HPJR")
    del app.tarif_jour_rouge_etat
    app._update_certified_measurement(T0)
    assert not app.chauffage_predictif_measurement_active
