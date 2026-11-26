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
