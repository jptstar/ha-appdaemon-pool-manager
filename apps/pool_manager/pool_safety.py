# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import datetime
from datetime import timedelta

from pool_common import TAB_MODE, heure_to_timedelta


def decide_hors_gel_continu(temp_ext, actif, mode, seuil_on=1.0, seuil_off=3.0):
    """Hysteretic continuous freeze protection decision."""
    if mode == TAB_MODE[4]:
        return False
    if temp_ext is None:
        return mode in [None, "", TAB_MODE[2]] or bool(actif)
    temp_ext = float(temp_ext)
    if temp_ext <= float(seuil_on):
        return True
    if temp_ext >= float(seuil_off):
        return False
    return bool(actif)


def pac_auto_conditions_demarrage(t_amb, t_24h, t_7j, seuils):
    if None in (t_amb, t_24h, t_7j):
        return False
    return (
        float(t_amb) > float(seuils[0])
        and float(t_24h) > float(seuils[1])
        and float(t_7j) > float(seuils[2])
    )


def pac_auto_conditions_arret(t_amb, t_24h, t_7j, seuils):
    if None in (t_amb, t_24h, t_7j):
        return False
    return (
        float(t_amb) < float(seuils[0])
        and float(t_24h) < float(seuils[1])
        and float(t_7j) < float(seuils[2])
    )


class SafetyMixin:
    """PAC sequencing, adaptive freeze protection and entity fail-safe logic."""

    def _initialize_safety(self):
        b = lambda key, default: str(self.args.get(key, default)).lower() == "true"
        self.fail_safe_active = b("fail_safe_active", "true")
        self.gestion_pac_auto = b("gestion_pac_auto", "false")
        self.hors_gel_adaptatif = b("hors_gel_adaptatif", "false")

        self.entity_temperature_exterieure = self.args.get("entity_temperature_exterieure")
        self.entity_temperature_exterieure_moyenne_24h = self.args.get("entity_temperature_exterieure_moyenne_24h")
        self.entity_temperature_exterieure_moyenne_7j = self.args.get("entity_temperature_exterieure_moyenne_7j")

        self.heure_debut_pac_auto = self.args.get("heure_debut_pac_auto", "08:00:00")
        self.heure_fin_pac_auto = self.args.get("heure_fin_pac_auto", "20:00:00")
        self.pac_auto_start_thresholds = (
            float(self.args.get("pac_auto_demarrage_ambiante_c", 25.0)),
            float(self.args.get("pac_auto_demarrage_24h_c", 19.0)),
            float(self.args.get("pac_auto_demarrage_7j_c", 18.0)),
        )
        self.pac_auto_stop_thresholds = (
            float(self.args.get("pac_auto_arret_ambiante_c", 21.0)),
            float(self.args.get("pac_auto_arret_24h_c", 18.0)),
            float(self.args.get("pac_auto_arret_7j_c", 17.0)),
        )
        self.pac_auto_timeout_capteurs_s = int(float(self.args.get("pac_auto_timeout_capteurs_s", 900)))
        self.pac_auto_timeout_demarrage_s = int(float(self.args.get("pac_auto_timeout_demarrage_s", 30)))
        self.pac_min_on_s = max(0, int(float(self.args.get("pac_min_on_s", 900))))
        self.pac_min_off_s = max(0, int(float(self.args.get("pac_min_off_s", 300))))
        self.pac_post_circulation_s = int(float(self.args.get("pac_post_circulation_s", 60)))
        self.pac_post_circulation_stable_s = max(
            0,
            int(float(self.args.get("pac_post_circulation_stable_s", 30))),
        )
        self.pac_post_circulation_max_s = max(
            self.pac_post_circulation_s,
            int(float(self.args.get("pac_post_circulation_max_s", 180))),
        )
        self.pac_flow_fail_timeout_s = int(float(self.args.get("pac_flow_fail_timeout_s", 15)))
        self.hors_gel_continu_on_c = float(self.args.get("hors_gel_continu_on_c", 1.0))
        self.hors_gel_continu_off_c = float(self.args.get("hors_gel_continu_off_c", 3.0))
        self.temperature_eau_secours_c = float(self.args.get("temperature_eau_secours_c", 32.0))

        self.hors_gel_continu_active = False
        self.last_valid_water_temp = None
        self.last_valid_pac_policy = None
        self.last_valid_pac_policy_at = None
        self.pac_auto_start_pending = False
        self.pac_auto_start_deadline = None
        self.handle_pac_auto_start = None
        self.pac_last_start_at = None
        self.pac_compressor_started_at = None
        self.pac_last_stop_at = None
        self.pac_deferred_stop_reason = None
        self.pac_deferred_start_label = None
        self.pac_post_circulation_until = None
        self.pac_post_circulation_low_since = None
        self.handle_pac_post_circulation = None
        self.pac_flow_fault_since = None
        self._safety_faults = set()

        self.pac_flow_active_w = float(
            self.args.get("pac_flow_active_w", self.args["seuil_pac_veille_w"])
        )

        for entity in (
            self.entity_temperature_exterieure,
            self.entity_temperature_exterieure_moyenne_24h,
            self.entity_temperature_exterieure_moyenne_7j,
        ):
            if entity:
                self.listen_state(self.safety_input_changed, entity)
        self.run_every(self.safety_tick, "now", 30)

    @staticmethod
    def _invalid(value):
        return value is None or (
            isinstance(value, str)
            and value.strip().lower() in {"unknown", "unavailable", "none", ""}
        )

    def _raw_float(self, entity):
        if not entity:
            return None
        try:
            value = self.get_state(entity)
            return None if self._invalid(value) else float(value)
        except Exception:
            return None

    def _available(self, entity):
        if not entity:
            return False
        try:
            return not self._invalid(self.get_state(entity))
        except Exception:
            return False

    def _fault(self, key, message):
        if key in self._safety_faults:
            return
        self._safety_faults.add(key)
        try:
            self.log(f"⚠️ Fail-safe: {message}", log="piscine_log")
        except Exception:
            pass

    def _recover(self, key, message=None):
        if key not in self._safety_faults:
            return
        self._safety_faults.discard(key)
        if message:
            try:
                self.log(f"✅ Fail-safe rétabli: {message}", log="piscine_log")
            except Exception:
                pass

    # Water probe: memory -> last valid -> conservative fallback, never fake 10 °C.
    def get_float_state(self, entity_id, default=0.0):
        if self.fail_safe_active and entity_id == self.args.get("temperature_eau"):
            current = self._raw_float(entity_id)
            if current is not None:
                self.last_valid_water_temp = current
                self._recover("temperature_eau")
                return current
            mem = self._raw_float(self.args.get("mem_temp"))
            if mem is not None:
                self._fault("temperature_eau", f"température eau indisponible, mémoire {mem:.1f} °C utilisée")
                return mem
            if self.last_valid_water_temp is not None:
                self._fault("temperature_eau", "température eau indisponible, dernière valeur valide utilisée")
                return float(self.last_valid_water_temp)
            self._fault("temperature_eau", f"température eau indisponible, secours {self.temperature_eau_secours_c:.1f} °C")
            return self.temperature_eau_secours_c
        return self._get_float_state_raw(entity_id, default)

    def _mode(self):
        try:
            value = self.get_state(self.args["mode_de_fonctionnement"])
            return None if self._invalid(value) else str(value).strip()
        except Exception:
            return None

    # ----------------------------- freeze protection -----------------------------
    def _freeze_state(self, mode):
        if not self.hors_gel_adaptatif:
            self.hors_gel_continu_active = False
            return False, self._raw_float(self.entity_temperature_exterieure)

        temp = self._raw_float(self.entity_temperature_exterieure)
        if temp is None:
            self._fault("temperature_exterieure", "température extérieure indisponible")
        else:
            self._recover("temperature_exterieure")
        self.hors_gel_continu_active = decide_hors_gel_continu(
            temp,
            self.hors_gel_continu_active,
            mode,
            self.hors_gel_continu_on_c,
            self.hors_gel_continu_off_c,
        )
        return self.hors_gel_continu_active, temp

    def _cancel_hors_gel_timer(self):
        if getattr(self, "handle_bras", None) is not None:
            try:
                self.cancel_timer(self.handle_bras)
            except Exception:
                pass
            self.handle_bras = None
            self.prochain_bras = None

    def _apply_freeze_continuous(self, temp):
        self._cancel_hors_gel_timer()
        self.stabilisation_active = False
        self.fin_stabilisation = None
        self.brassage_en_cours = True
        self.type_brassage = "hors_gel_continu"
        self.fin_brassage = None
        self.lock_text = True
        self._pac_off("hors gel sécurité", post=False)

        speed = max(self.vitesse_min_filtration_utile, self.vitesse_min_pac_active)
        if not self.pompe_est_on():
            self.start_pump_with_delayed_speed(speed, delay_s=2, context="hors_gel_continu")
        else:
            current = self.get_fan_percentage()
            if current is None or current < speed:
                self.set_pump_percentage(speed, force=True)
        self.maj_electrolyseur()

        if temp is None:
            self.set_messages("Hors gel fail-safe", f"T° extérieure indisponible | circulation continue {speed}%")
        else:
            self.set_messages("Hors gel sécurité", f"{temp:.1f} °C | circulation continue {speed}%")
        self.set_debug_w("")

    def _release_freeze(self, mode):
        if self.type_brassage == "hors_gel_continu":
            self.brassage_en_cours = False
            self.type_brassage = None
            self.fin_brassage = None
            self.lock_text = False
        if mode == TAB_MODE[2] and getattr(self, "handle_bras", None) is None:
            self.planifier_bras(0)

    def _electrolyse_securite_autorisee(self):
        return not getattr(self, "hors_gel_continu_active", False)

    def rebrassage_hors_gel(self, kwargs):
        mode = self._mode()
        active, temp = self._freeze_state(mode)
        if active and mode != TAB_MODE[4]:
            self._apply_freeze_continuous(temp)
            return
        return self._rebrassage_hors_gel_core(kwargs)

    # ------------------------------- PAC management -------------------------------
    def _pac_values(self):
        values = (
            self._raw_float(self.entity_temperature_exterieure),
            self._raw_float(self.entity_temperature_exterieure_moyenne_24h),
            self._raw_float(self.entity_temperature_exterieure_moyenne_7j),
        )
        now = datetime.datetime.now()
        if all(v is not None for v in values):
            self.last_valid_pac_policy = values
            self.last_valid_pac_policy_at = now
            self._recover("pac_policy_sensors")
            return values, True, False

        self._fault("pac_policy_sensors", "capteurs météo PAC indisponibles")
        expired = True
        if self.last_valid_pac_policy_at is not None:
            expired = (now - self.last_valid_pac_policy_at).total_seconds() > self.pac_auto_timeout_capteurs_s
        if self.last_valid_pac_policy is not None and not expired:
            return self.last_valid_pac_policy, False, False
        return (None, None, None), False, True

    def _pac_window(self):
        now = datetime.datetime.now()
        current = timedelta(hours=now.hour, minutes=now.minute, seconds=now.second)
        start = heure_to_timedelta(self.heure_debut_pac_auto)
        end = heure_to_timedelta(self.heure_fin_pac_auto)
        return start <= current < end if start <= end else current >= start or current < end

    def _pac_state(self):
        try:
            value = self.get_state(self.entity_pac_climate)
            return None if self._invalid(value) else str(value).lower()
        except Exception:
            return None

    def _pac_power_active(self):
        power = self._raw_float(getattr(self, "entity_pac_conso", None))
        return power is not None and power >= self.pac_flow_active_w

    def _pump_flow_ok(self):
        try:
            if self.get_state(self.args["cde_pompe"]) != "on":
                return False
        except Exception:
            return False
        speed = self.get_fan_percentage()
        return speed is not None and speed >= max(self.vitesse_min_filtration_utile, self.vitesse_min_pac_active)

    def _ensure_pac_flow(self):
        speed = max(self.vitesse_min_filtration_utile, self.vitesse_min_pac_active)
        if not self.pompe_est_on():
            if not self.start_sequence_is_running():
                self.start_pump_with_delayed_speed(speed, delay_s=2, context="pac_securite")
            return
        current = self.get_fan_percentage()
        if current is None or current < speed:
            self.set_pump_percentage(speed, force=True)

    def _cancel_pac_start(self):
        if self.handle_pac_auto_start is not None:
            try:
                self.cancel_timer(self.handle_pac_auto_start)
            except Exception:
                pass
        self.handle_pac_auto_start = None
        self.pac_auto_start_pending = False
        self.pac_auto_start_deadline = None

    def _pac_mark_started(self):
        self.pac_last_start_at = datetime.datetime.now()
        self.pac_compressor_started_at = None
        self.pac_deferred_stop_reason = None
        self.pac_deferred_start_label = None

    def _pac_mark_stopped(self):
        self.pac_last_stop_at = datetime.datetime.now()
        self.pac_compressor_started_at = None
        self.pac_deferred_stop_reason = None

    def _pac_min_on_remaining_s(self):
        started_at = getattr(self, "pac_compressor_started_at", None)
        if started_at is None or self.pac_min_on_s <= 0:
            return 0
        elapsed = (datetime.datetime.now() - started_at).total_seconds()
        return max(0, int(self.pac_min_on_s - elapsed))

    def _pac_min_off_remaining_s(self):
        stopped_at = getattr(self, "pac_last_stop_at", None)
        if stopped_at is None or self.pac_min_off_s <= 0:
            return 0
        elapsed = (datetime.datetime.now() - stopped_at).total_seconds()
        return max(0, int(self.pac_min_off_s - elapsed))

    def _pac_start_deferred(self, label):
        if self._pac_state() != "off":
            self.pac_deferred_start_label = None
            return False
        remaining = self._pac_min_off_remaining_s()
        if remaining <= 0:
            self.pac_deferred_start_label = None
            return False
        if self.pac_deferred_start_label != label:
            self.pac_deferred_start_label = label
            self.log(
                f"PAC: démarrage différé {remaining}s, anti-cycle ({label})",
                log="piscine_log",
            )
        return True

    def _request_pac_start(self):
        if self.pac_auto_start_pending:
            return
        if self._pac_start_deferred("automatique saisonnier"):
            return
        self.pac_auto_start_pending = True
        self.pac_auto_start_deadline = datetime.datetime.now() + timedelta(seconds=self.pac_auto_timeout_demarrage_s)
        self._ensure_pac_flow()
        self.handle_pac_auto_start = self.run_in(self._check_pac_start, 2)

    def _check_pac_start(self, kwargs):
        self.handle_pac_auto_start = None
        if not self.pac_auto_start_pending:
            return
        if self.gestion_pac_auto and not self.mode_auto_autorise():
            self._cancel_pac_start()
            return
        mode = self._mode()
        if self.derogation_chauffage_active() or mode not in [TAB_MODE[0], TAB_MODE[1]] or not self._pac_window():
            self._cancel_pac_start()
            return
        if self.pac_auto_start_deadline is None or datetime.datetime.now() >= self.pac_auto_start_deadline:
            self._fault("pac_start", "PAC non démarrée: circulation pompe non confirmée")
            self._cancel_pac_start()
            self.traitement({})
            return
        values, valid_now, _ = self._pac_values()
        if not valid_now or not pac_auto_conditions_demarrage(*values, self.pac_auto_start_thresholds):
            self._cancel_pac_start()
            self.traitement({})
            return
        if not self._available(self.entity_pac_climate):
            self._fault("pac_climate", "entité PAC indisponible, démarrage interdit")
            self._cancel_pac_start()
            return
        if self._pump_flow_ok():
            try:
                self.call_service("climate/set_hvac_mode", entity_id=self.entity_pac_climate, hvac_mode="heat")
                self._pac_mark_started()
                self._recover("pac_start")
                self._recover("pac_climate")
                self._cancel_pac_start()
                self.log("PAC auto: pompe >= minimum confirmée, PAC -> Heat", log="piscine_log")
            except Exception as exc:
                self._fault("pac_start", f"échec commande Heat: {exc}")
                self._cancel_pac_start()
            return
        self._ensure_pac_flow()
        self.handle_pac_auto_start = self.run_in(self._check_pac_start, 2)

    def _end_pac_post(self, kwargs):
        self.handle_pac_post_circulation = None
        now = datetime.datetime.now()
        until = getattr(self, "pac_post_circulation_until", None)
        power_active = self._pac_power_active()

        if power_active:
            self.pac_post_circulation_low_since = None
            if until is not None and now < until:
                self._ensure_pac_flow()
                self.handle_pac_post_circulation = self.run_in(
                    self._end_pac_post,
                    min(5, max(1, int((until - now).total_seconds()))),
                )
                return
        else:
            low_since = getattr(self, "pac_post_circulation_low_since", None)
            if low_since is None:
                self.pac_post_circulation_low_since = now
                low_since = now
            stable_s = max(
                0,
                int(getattr(self, "pac_post_circulation_stable_s", 0)),
            )
            low_elapsed = (now - low_since).total_seconds()
            if low_elapsed < stable_s and (until is None or now < until):
                self._ensure_pac_flow()
                self.handle_pac_post_circulation = self.run_in(
                    self._end_pac_post,
                    min(5, max(1, int(stable_s - low_elapsed))),
                )
                return

        self.pac_post_circulation_until = None
        self.pac_post_circulation_low_since = None
        self.traitement({})

    def _start_pac_post_circulation(self):
        if self.pac_post_circulation_s <= 0:
            return
        now = datetime.datetime.now()
        self.pac_post_circulation_until = now + timedelta(
            seconds=max(
                self.pac_post_circulation_s,
                getattr(
                    self,
                    "pac_post_circulation_max_s",
                    self.pac_post_circulation_s,
                ),
            )
        )
        self.pac_post_circulation_low_since = None
        if self.handle_pac_post_circulation is not None:
            try:
                self.cancel_timer(self.handle_pac_post_circulation)
            except Exception:
                pass
        self.handle_pac_post_circulation = self.run_in(
            self._end_pac_post,
            self.pac_post_circulation_s,
        )
        self._ensure_pac_flow()

    def _pac_off(self, reason, post=True, respect_min_on=False):
        self._cancel_pac_start()
        state = self._pac_state()
        if state is None:
            if self._pac_power_active():
                self._fault("pac_climate", f"PAC active mais climate indisponible ({reason})")
            return False
        self._recover("pac_climate")
        if state == "off":
            self.pac_deferred_stop_reason = None
            # Some PACs expose HVAC off before their compressor power has
            # actually fallen. Preserve circulation for that physical tail.
            if post and self._pac_power_active():
                self._start_pac_post_circulation()
            return True
        if respect_min_on:
            if self.pac_compressor_started_at is None and self._pac_power_active():
                # After an AppDaemon restart, protect a physically active
                # compressor conservatively instead of stopping it at once.
                self.pac_compressor_started_at = datetime.datetime.now()
            remaining = self._pac_min_on_remaining_s()
            if remaining > 0:
                if self.pac_deferred_stop_reason != reason:
                    self.pac_deferred_stop_reason = reason
                    self.log(
                        f"PAC: arrêt différé {remaining}s, anti-cycle ({reason})",
                        log="piscine_log",
                    )
                return False
        try:
            self.call_service("climate/set_hvac_mode", entity_id=self.entity_pac_climate, hvac_mode="off")
            self._pac_mark_stopped()
            self.log(f"PAC auto: arrêt ({reason})", log="piscine_log")
        except Exception as exc:
            self._fault("pac_stop", f"échec arrêt PAC: {exc}")
            return False

        if post:
            self._start_pac_post_circulation()
        return True

    def _pac_post_active(self):
        until = getattr(self, "pac_post_circulation_until", None)
        if until is None:
            return False
        if datetime.datetime.now() >= until:
            self.pac_post_circulation_until = None
            self.pac_post_circulation_low_since = None
            return False
        return True

    def _manage_pac_saisonnier(self):
        if not self.gestion_pac_auto:
            return
        if not self.mode_auto_autorise():
            self._cancel_pac_start()
            return
        mode = self._mode()
        if mode is None:
            self._fault("mode", "mode piscine indisponible")
            self._pac_off("mode indisponible")
            return
        self._recover("mode")
        if self.derogation_chauffage_active():
            self._cancel_pac_start()
            return
        if mode in [TAB_MODE[2], TAB_MODE[4]]:
            self._pac_off("hors gel / arrêt forcé", post=(mode != TAB_MODE[4]))
            return
        if mode not in [TAB_MODE[0], TAB_MODE[1]]:
            self._cancel_pac_start()
            return
        if not self._pac_window():
            self._pac_off("hors plage 20:00-08:00")
            return

        (t_amb, t_24h, t_7j), valid_now, expired = self._pac_values()
        state = self._pac_state()
        if state is None:
            self._fault("pac_climate", "entité PAC indisponible, démarrage interdit")
            self._cancel_pac_start()
            return
        self._recover("pac_climate")
        if not valid_now:
            self._cancel_pac_start()
            if expired and state != "off":
                self._pac_off("capteurs météo indisponibles")
            return
        if state == "off":
            if pac_auto_conditions_demarrage(t_amb, t_24h, t_7j, self.pac_auto_start_thresholds):
                self._request_pac_start()
            return
        if pac_auto_conditions_arret(t_amb, t_24h, t_7j, self.pac_auto_stop_thresholds):
            self._pac_off("conditions froides")

    def _protect_pac_flow(self):
        pac_power_active = self._pac_power_active()
        if pac_power_active and self.pac_compressor_started_at is None:
            self.pac_compressor_started_at = datetime.datetime.now()
        if not self.fail_safe_active or not pac_power_active:
            self.pac_flow_fault_since = None
            self._recover("pac_flow")
            return
        if self._pump_flow_ok():
            self.pac_flow_fault_since = None
            self._recover("pac_flow")
            return

        self._ensure_pac_flow()
        now = datetime.datetime.now()
        if self.pac_flow_fault_since is None:
            self.pac_flow_fault_since = now
            self._fault("pac_flow", "PAC consomme mais circulation pompe non confirmée")
            return
        if (now - self.pac_flow_fault_since).total_seconds() >= self.pac_flow_fail_timeout_s:
            self._pac_off("circulation pompe non confirmée", post=False)

    def _pac_circulation_securite_requise(self):
        if (
            getattr(self, "pac_auto_start_pending", False)
            or getattr(self, "pac_deferred_stop_reason", None) is not None
            or self._pac_post_active()
        ):
            return True
        if getattr(self, "fail_safe_active", False) and self._pac_power_active():
            return True
        return False

    # -------------------------------- entry points --------------------------------
    def safety_input_changed(self, entity, attribute, old, new, kwargs):
        self.safety_tick({})

    def safety_tick(self, kwargs):
        try:
            self._protect_pac_flow()
            self._manage_pac_auto()
            self.traitement({})
        except Exception as exc:
            self._fault("safety_tick", f"erreur boucle sécurité: {exc}")

    def traitement(self, kwargs):
        mode = self._mode()
        if mode is None:
            self._fault("mode", "mode piscine indisponible")
            if self.hors_gel_adaptatif:
                active, temp = self._freeze_state(None)
                if active:
                    self._apply_freeze_continuous(temp)
            return
        self._recover("mode")

        if mode == TAB_MODE[4] or self.arret_force_actif():
            if self.gestion_pac_auto or self._pac_power_active():
                self._pac_off("arrêt forcé", post=False)
            self.hors_gel_continu_active = False
            self._release_freeze(mode)
            return self._traitement_filtration(kwargs)

        if self.gestion_pac_auto and mode == TAB_MODE[2]:
            self._pac_off("mode hors gel")

        active, temp = self._freeze_state(mode)
        if active:
            self._apply_freeze_continuous(temp)
            return
        self._release_freeze(mode)
        return self._traitement_filtration(kwargs)
