# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import datetime
from datetime import timedelta

from pool_common import *

class DevicesMixin:

    def get_float_state(self, entity_id, default=0.0):
        try:
            val = self.get_state(entity_id)
            if val in [None, "unknown", "unavailable", ""]:
                return default
            return float(val)
        except Exception:
            return default

    def get_fan_percentage(self):
        try:
            val = self.get_state(self.args["fan_variateur_pompe"], attribute="percentage")
            if val in [None, "unknown", "unavailable", ""]:
                return None
            return int(float(val))
        except Exception:
            return None

    def get_pompe_power_reelle(self):
        return self.get_float_state(self.entity_pompe_conso, 0.0)

    def get_reseau_net_w(self):
        return self.get_float_state(self.args["restitution_inst"], 0.0)

    def get_surplus_depuis_reseau_net(self):
        puissance_reseau_nette = self.get_reseau_net_w()
        return max(0.0, -puissance_reseau_nette - self.marge_surplus_securite)

    def get_pv_power(self):
        if not self.entity_pv_power:
            return None
        return self.get_float_state(self.entity_pv_power, 0.0)

    def reset_pid(self):
        self.pid_integral = 0.0
        self.pid_last_error = 0.0
        self.pid_last_time = None

    def calcule_vitesse_pid_hybride(self, puissance_reseau_nette, vitesse_actuelle):
        now = datetime.datetime.now()

        if vitesse_actuelle is None:
            vitesse_actuelle = self.derniere_vitesse_commande
        if vitesse_actuelle is None:
            vitesse_actuelle = self.vitesse_min_filtration_utile

        if self.pid_last_time is None:
            dt = 1.0
        else:
            dt = max(1.0, (now - self.pid_last_time).total_seconds())

        erreur = self.pid_target_w - float(puissance_reseau_nette)
        erreur_effective = 0.0 if abs(erreur) <= self.pid_deadband_w else erreur

        self.pid_integral += erreur_effective * dt
        self.pid_integral = max(self.pid_integral_min, min(self.pid_integral_max, self.pid_integral))

        derivee = (erreur_effective - self.pid_last_error) / dt
        sortie = self.pid_kp * erreur_effective + self.pid_ki * self.pid_integral + self.pid_kd * derivee
        pas = max(-self.pid_step_max, min(self.pid_step_max, sortie))

        vitesse_cible = float(vitesse_actuelle) + pas
        vitesse_cible = max(self.vitesse_min_filtration_utile, min(self.vitesse_max_solaire, vitesse_cible))

        self.pid_last_error = erreur_effective
        self.pid_last_time = now

        return int(round(vitesse_cible)), erreur, pas

    def format_progress(self, filtre, objectif):
        return f"{float(filtre):.1f}/{float(objectif):.1f}h"

    def split_message_solaire(self, etat, vitesse, filtre, objectif, extra=""):
        debit = self.debit_pompe(vitesse)
        short_msg = f"{etat} | {filtre:.1f}/{objectif:.1f}h"

        detail_parts = [
            f"{int(vitesse)}%",
            f"{debit:.1f}m3/h",
            f"{filtre:.1f}/{objectif:.1f}h"
        ]
        if extra:
            detail_parts.append(extra)

        return short_msg, " | ".join(detail_parts)

    def split_message_simple(self, titre, filtre, objectif, extra=""):
        short_msg = f"{titre} | {filtre:.1f}/{objectif:.1f}h"
        detail_parts = [f"{filtre:.1f}/{objectif:.1f}h"]
        if extra:
            detail_parts.append(extra)
        return short_msg, " | ".join(detail_parts)

    def set_pump_percentage(self, percentage, force=False):
        percentage = int(max(0, min(100, percentage)))
        now = datetime.datetime.now()

        current = self.get_fan_percentage()
        if current is None:
            current = self.derniere_vitesse_commande if self.derniere_vitesse_commande is not None else percentage

        if not force and abs(percentage - current) < self.delta_vitesse_min:
            return current

        if not force and (now - self.last_changement_vitesse).total_seconds() < self.tempo_changement_vitesse:
            return current

        if not force:
            if percentage > current:
                percentage = min(current + self.pas_vitesse_max, percentage)
            elif percentage < current:
                percentage = max(current - self.pas_vitesse_max, percentage)

        try:
            self.call_service("fan/set_percentage", entity_id=self.args["fan_variateur_pompe"], percentage=percentage)
            self.derniere_vitesse_commande = percentage
            self.last_changement_vitesse = now
            self.maj_electrolyseur()
            return percentage
        except Exception as e:
            self.log(f"⚠️ Erreur set_percentage : {e}", log="piscine_log")
            return current

    def pompe_est_on(self):
        return self.get_state(self.args["cde_pompe"]) == "on"

    def temps_depuis_on(self):
        return (datetime.datetime.now() - self.last_pompe_on).total_seconds()

    def temps_depuis_off(self):
        return (datetime.datetime.now() - self.last_pompe_off).total_seconds()

    def turn_on_pompe_mem(self):
        self.cancel_pending_stop_sequence()
        if not self.pompe_est_on():
            self.call_service("fan/turn_on", entity_id=self.args["cde_pompe"])
            self.last_pompe_on = datetime.datetime.now()
        self.maj_electrolyseur()

    def turn_off_pompe_mem(self, force=False):
        self.cancel_pending_start_sequence()

        if force or self.arret_force_actif():
            self.cancel_pending_stop_sequence()
            self.set_consigne_electrolyseur(self.consigne_electrolyseur_arret, force=True)
            self.turn_off_pompe_direct()
            return

        if not self.pompe_est_on():
            self.set_consigne_electrolyseur(self.consigne_electrolyseur_arret)
            self.cancel_pending_stop_sequence()
            self.set_debug_w("")
            return

        if self.stop_sequence_is_running():
            return

        # Arrêt normal : on coupe d'abord l'électrolyseur puis on laisse circuler l'eau.
        self.set_consigne_electrolyseur(self.consigne_electrolyseur_arret, force=True)
        self.stop_sequence_active = True
        self.stop_sequence_until = datetime.datetime.now() + timedelta(seconds=self.tempo_arret_electrolyseur_s)
        self.handle_delayed_stop = self.run_in(self.apply_pending_stop_after_electrolyseur, self.tempo_arret_electrolyseur_s)

    def turn_off_pompe_direct(self):
        if self.pompe_est_on():
            self.call_service("fan/turn_off", entity_id=self.args["cde_pompe"])
            self.last_pompe_off = datetime.datetime.now()
            self.derniere_vitesse_commande = None

        self.set_debug_w("")

    def apply_pending_stop_after_electrolyseur(self, kwargs):
        try:
            self.set_consigne_electrolyseur(self.consigne_electrolyseur_arret, force=True)
            self.turn_off_pompe_direct()
        finally:
            self.handle_delayed_stop = None
            self.stop_sequence_active = False
            self.stop_sequence_until = None

    def cancel_pending_stop_sequence(self):
        if self.handle_delayed_stop is not None:
            try:
                self.cancel_timer(self.handle_delayed_stop)
            except Exception:
                pass
            self.handle_delayed_stop = None

        self.stop_sequence_active = False
        self.stop_sequence_until = None

    def stop_sequence_is_running(self):
        if not self.stop_sequence_active:
            return False
        if self.stop_sequence_until is None:
            return False
        if datetime.datetime.now() > self.stop_sequence_until:
            self.stop_sequence_active = False
            self.stop_sequence_until = None
            self.handle_delayed_stop = None
            return False
        return True

    def arret_force_actif(self):
        if "arret_force" in self.args and self.get_state(self.args["arret_force"]) == "on":
            return True
        mode = (self.get_state(self.args["mode_de_fonctionnement"]) or "").strip()
        return mode == TAB_MODE[4]

    def set_consigne_electrolyseur(self, valeur, force=False):
        if not self.entity_consigne_electrolyseur:
            return

        try:
            valeur = max(0.0, min(100.0, float(valeur)))
        except Exception:
            valeur = 0.0

        if not force and self.last_consigne_electrolyseur is not None:
            if abs(float(self.last_consigne_electrolyseur) - valeur) < 0.01:
                return

        try:
            self.call_service("number/set_value", entity_id=self.entity_consigne_electrolyseur, value=valeur)
            self.last_consigne_electrolyseur = valeur
        except Exception as e:
            self.log(f"⚠️ Erreur consigne électrolyseur : {e}", log="piscine_log")

    def get_consigne_electrolyseur_volet_ouvert(self):
        return self.get_float_state(
            self.entity_consigne_electrolyseur_volet_ouvert,
            self.consigne_electrolyseur_volet_ouvert_defaut
        )

    def get_consigne_electrolyseur_volet_ferme(self):
        return self.get_float_state(
            self.entity_consigne_electrolyseur_volet_ferme,
            self.consigne_electrolyseur_volet_ferme_defaut
        )

    def volet_piscine_ouvert(self):
        if not self.entity_volet_piscine:
            return False

        etat_volet = self.get_state(self.entity_volet_piscine)
        return etat_volet in ["open", "opening"]

    def electrolyseur_autorise(self):
        if self.arret_force_actif():
            return False

        if not self.pompe_est_on():
            return False

        mode = (self.get_state(self.args["mode_de_fonctionnement"]) or "").strip()

        # Hors Gel : jamais d'électrolyse, eau froide.
        if mode == TAB_MODE[2]:
            return False

        if self.brassage_en_cours and self.type_brassage == "hors_gel":
            return False

        mode_autorise = mode in [TAB_MODE[0], TAB_MODE[1], TAB_MODE[3]]
        brassage_nuit_autorise = self.brassage_en_cours and self.type_brassage == "nuit"

        if not mode_autorise and not brassage_nuit_autorise:
            return False

        vitesse = self.get_fan_percentage()
        if vitesse is None:
            vitesse = self.derniere_vitesse_commande

        if vitesse is None:
            return False

        return int(float(vitesse)) >= self.seuil_vitesse_electrolyseur_pct

    def maj_electrolyseur(self):
        if not self.entity_consigne_electrolyseur:
            return

        if not self.electrolyseur_autorise():
            self.set_consigne_electrolyseur(self.consigne_electrolyseur_arret)
            return

        if self.volet_piscine_ouvert():
            self.set_consigne_electrolyseur(self.get_consigne_electrolyseur_volet_ouvert())
        else:
            self.set_consigne_electrolyseur(self.get_consigne_electrolyseur_volet_ferme())

    def cancel_pending_start_sequence(self):
        if self.handle_apply_speed is not None:
            try:
                self.cancel_timer(self.handle_apply_speed)
            except Exception:
                pass
            self.handle_apply_speed = None

        self.pending_start_context = None
        self.start_sequence_active = False
        self.start_sequence_until = None

    def start_pump_with_delayed_speed(self, percentage, delay_s=2, context=""):
        percentage = int(max(0, min(100, percentage)))

        if self.start_sequence_active:
            return

        self.cancel_pending_start_sequence()
        self.start_sequence_active = True
        self.start_sequence_until = datetime.datetime.now() + timedelta(seconds=delay_s + 3)
        self.pending_start_context = {"percentage": percentage, "context": context}

        self.turn_on_pompe_mem()
        self.handle_apply_speed = self.run_in(self.apply_pending_speed_after_start, delay_s)

    def apply_pending_speed_after_start(self, kwargs):
        ctx = self.pending_start_context or {}
        percentage = int(ctx.get("percentage", self.vitesse_min_filtration_utile))

        try:
            self.set_pump_percentage(percentage, force=True)
            self.maj_electrolyseur()
        finally:
            self.handle_apply_speed = None
            self.pending_start_context = None
            self.start_sequence_active = False
            self.start_sequence_until = None

    def start_sequence_is_running(self):
        if not self.start_sequence_active:
            return False
        if self.start_sequence_until is None:
            return False
        if datetime.datetime.now() > self.start_sequence_until:
            self.start_sequence_active = False
            self.start_sequence_until = None
            self.pending_start_context = None
            return False
        return True

    def debit_pompe(self, vitesse):
        v = max(float(self.vitesse_min_filtration_utile), min(100.0, float(vitesse)))
        if v <= self.vitesse_min_filtration_utile:
            return self.debit_min_filtration_utile_m3h

        ratio = (v - self.vitesse_min_filtration_utile) / (100.0 - self.vitesse_min_filtration_utile)
        return self.debit_min_filtration_utile_m3h + ratio * (self.debit_max_pompe_m3h - self.debit_min_filtration_utile_m3h)

    def est_nuit(self, now_dt=None):
        if now_dt is None:
            now_dt = datetime.datetime.now()
        heure_actuelle = now_dt.time()
        return heure_actuelle >= datetime.time(22, 0, 0) or heure_actuelle < datetime.time(7, 0, 0)

    def doit_compter_filtration(self, now_dt=None):
        if now_dt is None:
            now_dt = datetime.datetime.now()

        if self.get_state(self.args["cde_pompe"]) != "on":
            return False

        mode = (self.get_state(self.args["mode_de_fonctionnement"]) or "").strip()
        nuit = self.est_nuit(now_dt)

        if self.brassage_en_cours and self.type_brassage == "nuit":
            return False

        if nuit and mode == TAB_MODE[3]:
            return False

        return True

    def reset_cumul_si_nouveau_jour(self):
        today = datetime.datetime.now().date()
        if today != self.date_cumul:
            self.date_cumul = today
            self.volume_filtre_jour = 0.0
            self.temps_filtration_equivalent_jour = 0.0
            self.derniere_maj_volume = datetime.datetime.now()

            self.set_value(self.entity_volume_filtre_jour, 0.0)
            self.set_value(self.entity_temps_filtration_equivalent_jour, 0.0)
            self.set_textvalue(self.entity_date_cumul, today.isoformat())

    def maj_cumul_filtration(self):
        self.reset_cumul_si_nouveau_jour()

        now = datetime.datetime.now()
        delta_s = (now - self.derniere_maj_volume).total_seconds()
        self.derniere_maj_volume = now

        if delta_s <= 0:
            return

        if not self.doit_compter_filtration(now):
            return

        vitesse = self.get_fan_percentage()
        if vitesse is None:
            vitesse = self.derniere_vitesse_commande if self.derniere_vitesse_commande is not None else 100

        debit = self.debit_pompe(vitesse)
        volume = debit * (delta_s / 3600.0)
        self.volume_filtre_jour += volume

        vitesse_ref = max(self.vitesse_min_filtration_utile, self.vitesse_reference_filtration)
        heures_reelles = delta_s / 3600.0
        temps_eq = heures_reelles * (float(vitesse) / float(vitesse_ref))
        self.temps_filtration_equivalent_jour += temps_eq

        self.set_value(self.entity_volume_filtre_jour, round(self.volume_filtre_jour, 2))
        self.set_value(self.entity_temps_filtration_equivalent_jour, round(self.temps_filtration_equivalent_jour, 2))
        self.set_textvalue(self.entity_date_cumul, self.date_cumul.isoformat())

    def calcule_vitesse_solaire(self, surplus_w):
        try:
            surplus = float(surplus_w)
        except Exception:
            return self.vitesse_min_solaire

        if surplus <= self.seuil_surplus_demarrage_w:
            return self.vitesse_min_solaire
        if surplus >= self.surplus_max_w:
            return self.vitesse_max_solaire

        ratio = (surplus - self.seuil_surplus_demarrage_w) / (self.surplus_max_w - self.seuil_surplus_demarrage_w)
        vitesse = self.vitesse_min_solaire + ratio * (self.vitesse_max_solaire - self.vitesse_min_solaire)
        return int(max(self.vitesse_min_solaire, min(self.vitesse_max_solaire, round(vitesse))))

    def puissance_pompe_estimee(self, vitesse):
        try:
            v = int(round(float(vitesse)))
        except Exception:
            return None

        points = sorted(self.points_puissance_pompe, key=lambda x: x[0])
        if not points:
            return None

        if v <= points[0][0]:
            return float(points[0][1])
        if v >= points[-1][0]:
            return float(points[-1][1])

        for i in range(len(points) - 1):
            v1, p1 = points[i]
            v2, p2 = points[i + 1]
            if v1 <= v <= v2:
                if v2 == v1:
                    return float(p1)
                ratio = (v - v1) / (v2 - v1)
                return float(p1 + ratio * (p2 - p1))

        return None

    def puissance_pompe_affichee(self, vitesse):
        puissance_reelle = self.get_pompe_power_reelle()
        if puissance_reelle >= 50:
            return puissance_reelle, True

        puissance_estimee = self.puissance_pompe_estimee(vitesse)
        return puissance_estimee, False

    def vitesse_solaire_maintien(self, surplus_net):
        try:
            surplus = float(surplus_net)
        except Exception:
            return self.vitesse_min_filtration_utile

        if surplus <= 0:
            return self.vitesse_min_filtration_utile

        return max(
            self.calcule_vitesse_solaire(max(surplus, self.seuil_surplus_demarrage_w)),
            self.vitesse_min_filtration_utile
        )

    def td_now(self):
        now = datetime.datetime.now()
        return timedelta(hours=now.hour, minutes=now.minute, seconds=now.second)

    def est_dans_plage(self, debut_txt, fin_txt):
        now_td = self.td_now()
        debut_td = heure_to_timedelta(debut_txt)
        fin_td = heure_to_timedelta(fin_txt)
        return debut_td <= now_td <= fin_td

    def est_dans_plage_pac_soleil(self):
        return self.est_dans_plage(self.heure_debut_pac_soleil, self.heure_fin_pac_soleil)

    def pac_mode_prioritaire_soleil(self):
        if not self.pac_priorite_soleil:
            return False
        if not self.est_dans_plage_pac_soleil():
            return False
        if not self.pac_besoin_chauffe():
            return False
        return True

    def import_reseau_actuel_w(self):
        reseau = self.get_reseau_net_w()
        return max(0.0, reseau)

    def surplus_equivalent_pac_jour(self, surplus_net):
        if not self.pac_mode_prioritaire_soleil():
            return max(0.0, surplus_net)

        import_w = self.import_reseau_actuel_w()
        marge_restante = max(0.0, self.pac_import_max_jour_w - import_w)
        return max(0.0, surplus_net + marge_restante)

    def tempo_anti_coupure_active(self):
        if self.pac_mode_prioritaire_soleil():
            return self.pac_tempo_anti_coupure_jour
        return self.tempo_anti_coupure_solaire

    def pac_autorisee(self):
        try:
            state = self.get_state(self.entity_pac_climate)
            if state in [None, "unknown", "unavailable"]:
                return None
            if state == "off":
                return False

            hvac_mode = self.get_state(self.entity_pac_climate, attribute="hvac_mode")
            if hvac_mode in [None, "unknown", "unavailable"]:
                return state != "off"

            return hvac_mode != "off"
        except Exception:
            return None

    def etat_pac(self):
        pac_ok = self.pac_autorisee()
        conso = self.get_float_state(self.entity_pac_conso, 0.0)

        if pac_ok is None:
            if conso >= self.seuil_pac_turbo_w:
                return "turbo"
            elif conso >= self.seuil_pac_smart_w:
                return "smart"
            elif conso >= self.seuil_pac_silent_w:
                return "silent"
            elif conso >= self.seuil_pac_veille_w:
                return "veille"
            else:
                return "off"

        if pac_ok is False:
            return "off"

        if conso < self.seuil_pac_veille_w:
            return "veille"
        elif conso < self.seuil_pac_smart_w:
            return "silent"
        elif conso < self.seuil_pac_turbo_w:
            return "smart"
        else:
            return "turbo"

    def pac_consigne(self):
        try:
            val = self.get_state(self.entity_pac_climate, attribute="temperature")
            if val in [None, "unknown", "unavailable", ""]:
                return None
            return float(val)
        except Exception:
            return None

    def pac_temperature_actuelle(self):
        try:
            val = self.get_state(self.entity_pac_climate, attribute="current_temperature")
            if val in [None, "unknown", "unavailable", ""]:
                return None
            return float(val)
        except Exception:
            return None

    def pac_besoin_chauffe(self):
        pac_ok = self.pac_autorisee()
        if pac_ok is not True:
            return False

        etat = self.etat_pac()
        if etat in ["silent", "smart", "turbo"]:
            return True

        consigne = self.pac_consigne()
        actuelle = self.pac_temperature_actuelle()
        if consigne is None or actuelle is None:
            return False

        return actuelle < (consigne - self.marge_temp_pac_c)

    def calcule_surplus_net_avec_pac(self, surplus_brut):
        etat = self.etat_pac()
        conso_pac = self.get_float_state(self.entity_pac_conso, 0.0)

        if etat in ["off", "veille"]:
            return max(0.0, surplus_brut), etat, conso_pac

        return max(0.0, surplus_brut - conso_pac), etat, conso_pac
