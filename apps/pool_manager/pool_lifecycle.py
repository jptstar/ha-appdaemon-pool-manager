# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import datetime
from datetime import timedelta

from pool_common import *

class LifecycleMixin:

    def initialize(self):
        self.fin_tempo = 0

        self.stabilisation_active = False
        self.fin_stabilisation = None
        self.stabilisation_deja_faite = False

        self.brassage_en_cours = False
        self.type_brassage = None
        self.fin_brassage = None

        self.mode_actif = None
        self.lock_text = False

        self.handle_bras = None
        self.prochain_bras = None

        self.debut_manque_soleil = None
        self.debut_stabilite_surplus = None

        self.handle_apply_speed = None
        self.pending_start_context = None
        self.start_sequence_active = False
        self.start_sequence_until = None

        # Arrêt pompe différé pour purge électrolyseur
        self.handle_delayed_stop = None
        self.stop_sequence_active = False
        self.stop_sequence_until = None

        self.last_night_slot = None

        self.last_message_filtration = None
        self.last_message_detail = None
        self.last_debug_message = None

        self.entity_message_detail = self.args.get("message_filtration_piscine_detail")

        # ÉLECTROLYSEUR
        self.entity_volet_piscine = self.args.get("entity_volet_piscine")
        self.entity_consigne_electrolyseur = self.args.get("entity_consigne_electrolyseur")
        self.entity_consigne_electrolyseur_volet_ouvert = self.args.get("entity_consigne_electrolyseur_volet_ouvert")
        self.entity_consigne_electrolyseur_volet_ferme = self.args.get("entity_consigne_electrolyseur_volet_ferme")
        self.consigne_electrolyseur_volet_ouvert_defaut = float(self.args.get("consigne_electrolyseur_volet_ouvert_defaut", 10))
        self.consigne_electrolyseur_volet_ferme_defaut = float(self.args.get("consigne_electrolyseur_volet_ferme_defaut", 20))
        self.consigne_electrolyseur_arret = float(self.args.get("consigne_electrolyseur_arret", 0))
        self.seuil_vitesse_electrolyseur_pct = int(float(self.args.get("seuil_vitesse_electrolyseur_pct", 47)))
        self.tempo_arret_electrolyseur_s = int(float(self.args.get("tempo_arret_electrolyseur_s", 10)))
        self.last_consigne_electrolyseur = None

        self.debit_max_pompe_m3h = float(self.args["debit_max_pompe_m3h"])
        self.debit_min_filtration_utile_m3h = float(self.args["debit_min_filtration_utile_m3h"])
        self.volume_piscine_m3 = float(self.args["volume_piscine_m3"])

        self.vitesse_mode_temperature = int(float(self.args["vitesse_mode_temperature"]))
        self.vitesse_reference_filtration = int(float(self.args["vitesse_reference_filtration"]))
        self.vitesse_min_filtration_utile = int(float(self.args["vitesse_min_filtration_utile"]))
        self.vitesse_min_solaire = int(float(self.args["vitesse_min_solaire"]))
        self.vitesse_max_solaire = int(float(self.args["vitesse_max_solaire"]))
        self.vitesse_rattrapage = int(float(self.args["vitesse_rattrapage"]))
        self.vitesse_min_rattrapage = int(float(self.args["vitesse_min_rattrapage"]))
        self.vitesse_rattrapage_palier_2 = int(float(self.args["vitesse_rattrapage_palier_2"]))
        self.vitesse_rattrapage_palier_3 = int(float(self.args["vitesse_rattrapage_palier_3"]))
        self.vitesse_rattrapage_palier_4 = int(float(self.args["vitesse_rattrapage_palier_4"]))
        self.vitesse_brassage_nuit = int(float(self.args.get("vitesse_brassage_nuit", self.vitesse_min_filtration_utile)))

        self.vitesse_max_maintien_faible = int(float(self.args.get("vitesse_max_maintien_faible", 60)))
        self.vitesse_max_maintien_faible_pac = int(float(self.args.get("vitesse_max_maintien_faible_pac", 75)))
        self.vitesse_max_rattrapage_surplus_faible = int(float(self.args.get("vitesse_max_rattrapage_surplus_faible", 70)))

        self.seuil_surplus_demarrage_w = float(self.args["seuil_surplus_demarrage_w"])
        self.seuil_surplus_arret_w = float(self.args["seuil_surplus_arret_w"])
        self.surplus_max_w = float(self.args["surplus_max_w"])
        self.marge_surplus_securite = float(self.args.get("marge_surplus_securite", 0))

        self.tempo_min_on_solaire = int(float(self.args["tempo_min_on_solaire"]))
        self.tempo_min_off_solaire = int(float(self.args["tempo_min_off_solaire"]))
        self.tempo_anti_coupure_solaire = int(float(self.args["tempo_anti_coupure_solaire"]))
        self.tempo_stabilite_surplus = int(float(self.args.get("tempo_stabilite_surplus", 90)))

        self.heure_debut_solaire = self.args["heure_debut_solaire"]
        self.heure_fin_solaire = self.args["heure_fin_solaire"]
        self.heure_fin_rattrapage = self.args["heure_fin_rattrapage"]

        self.entity_pac_climate = self.args["entity_pac_climate"]
        self.entity_pac_conso = self.args["entity_pac_conso"]
        self.entity_pompe_conso = self.args["entity_pompe_conso"]
        self.entity_pv_power = self.args.get("entity_pv_power")
        self.entity_debug_w = self.args.get("entity_debug_w")

        self.seuil_pac_veille_w = float(self.args["seuil_pac_veille_w"])
        self.seuil_pac_silent_w = float(self.args["seuil_pac_silent_w"])
        self.seuil_pac_smart_w = float(self.args["seuil_pac_smart_w"])
        self.seuil_pac_turbo_w = float(self.args["seuil_pac_turbo_w"])
        self.vitesse_min_pac_active = int(float(self.args["vitesse_min_pac_active"]))
        self.marge_temp_pac_c = float(self.args["marge_temp_pac_c"])

        self.pac_priorite_soleil = str(self.args.get("pac_priorite_soleil", "false")).lower() == "true"
        self.heure_debut_pac_soleil = self.args.get("heure_debut_pac_soleil", "10:00:00")
        self.heure_fin_pac_soleil = self.args.get("heure_fin_pac_soleil", "17:30:00")
        self.pac_import_max_jour_w = float(self.args.get("pac_import_max_jour_w", 300))
        self.pac_tempo_anti_coupure_jour = int(float(self.args.get("pac_tempo_anti_coupure_jour", self.tempo_anti_coupure_solaire)))
        self.pac_maintien_jour_force = str(self.args.get("pac_maintien_jour_force", "true")).lower() == "true"

        # GARANTIE QUOTA JOURNALIER
        self.garantir_quota_journalier = str(self.args.get("garantir_quota_journalier", "true")).lower() == "true"
        self.heure_limite_quota_journalier = self.args.get("heure_limite_quota_journalier", "23:50:00")
        self.vitesse_min_garantie_quota = int(float(self.args.get("vitesse_min_garantie_quota", self.vitesse_min_filtration_utile)))
        self.vitesse_max_garantie_quota = int(float(self.args.get("vitesse_max_garantie_quota", self.vitesse_max_solaire)))

        # PRIORITÉ PAC ABSOLUE
        self.pac_prioritaire_absolue = str(self.args.get("pac_prioritaire_absolue", "true")).lower() == "true"
        self.vitesse_pac_prioritaire = int(float(self.args.get("vitesse_pac_prioritaire", self.vitesse_min_pac_active)))

        self.reste_min_complement_soir_h = float(self.args["reste_min_complement_soir_h"])
        self.avancement_min_sans_complement = float(self.args["avancement_min_sans_complement"])
        self.vitesse_max_complement_soir = int(float(self.args["vitesse_max_complement_soir"]))

        self.seuil_ratio_rattrapage_1 = float(self.args["seuil_ratio_rattrapage_1"])
        self.seuil_ratio_rattrapage_2 = float(self.args["seuil_ratio_rattrapage_2"])
        self.seuil_ratio_rattrapage_3 = float(self.args["seuil_ratio_rattrapage_3"])
        self.seuil_ratio_rattrapage_4 = float(self.args["seuil_ratio_rattrapage_4"])

        self.entity_volume_filtre_jour = self.args["entity_volume_filtre_jour"]
        self.entity_temps_filtration_equivalent_jour = self.args["entity_temps_filtration_equivalent_jour"]
        self.entity_date_cumul = self.args["entity_date_cumul"]

        self.delta_vitesse_min = int(float(self.args["delta_vitesse_min"]))
        self.pas_vitesse_max = int(float(self.args["pas_vitesse_max"]))
        self.tempo_changement_vitesse = int(float(self.args["tempo_changement_vitesse"]))
        self.last_changement_vitesse = datetime.datetime.now() - timedelta(seconds=self.tempo_changement_vitesse + 1)

        self.points_puissance_pompe = [(int(p[0]), float(p[1])) for p in self.args["points_puissance_pompe"]]

        self.pid_kp = float(self.args.get("pid_kp", 0.0))
        self.pid_ki = float(self.args.get("pid_ki", 0.0))
        self.pid_kd = float(self.args.get("pid_kd", 0.0))
        self.pid_target_w = float(self.args.get("pid_target_w", -30.0))
        self.pid_deadband_w = float(self.args.get("pid_deadband_w", 40.0))
        self.pid_integral_min = float(self.args.get("pid_integral_min", -4000.0))
        self.pid_integral_max = float(self.args.get("pid_integral_max", 4000.0))
        self.pid_step_max = float(self.args.get("pid_step_max", 12.0))

        self.pid_integral = 0.0
        self.pid_last_error = 0.0
        self.pid_last_time = None

        today = datetime.datetime.now().date()
        today_txt = today.isoformat()
        date_stockee = self.get_state(self.entity_date_cumul)

        if date_stockee == today_txt:
            self.volume_filtre_jour = self.get_float_state(self.entity_volume_filtre_jour, 0.0)
            self.temps_filtration_equivalent_jour = self.get_float_state(self.entity_temps_filtration_equivalent_jour, 0.0)
        else:
            self.volume_filtre_jour = 0.0
            self.temps_filtration_equivalent_jour = 0.0
            self.set_value(self.entity_volume_filtre_jour, 0.0)
            self.set_value(self.entity_temps_filtration_equivalent_jour, 0.0)
            self.set_textvalue(self.entity_date_cumul, today_txt)

        self.date_cumul = today
        self.derniere_maj_volume = datetime.datetime.now()
        self.derniere_vitesse_commande = None

        # Reprise non destructive après un reload AppDaemon/HACS.
        # On conserve l'état réel de la pompe au lieu de supposer qu'elle est arrêtée.
        now_init = datetime.datetime.now()
        if self.get_state(self.args["cde_pompe"]) == "on":
            self.last_pompe_on = now_init
            self.last_pompe_off = now_init - timedelta(seconds=self.tempo_min_off_solaire + 1)
        else:
            self.last_pompe_off = now_init
            self.last_pompe_on = now_init - timedelta(seconds=self.tempo_min_on_solaire + 1)

        self.derniere_vitesse_commande = self.get_fan_percentage()

        self.listen_state(self.change_temp, self.args["temperature_eau"])
        self.listen_state(self.change_mode, self.args["mode_de_fonctionnement"])
        self.listen_state(self.change_coef, self.args["coef"])
        self.listen_state(self.ecretage_h_pivot, self.args["h_pivot"])
        self.listen_state(self.change_mode_calcul, self.args["mode_calcul"])

        self.listen_state(self.change_solaire, self.args["restitution_inst"])
        self.listen_state(self.change_solaire, self.entity_pac_conso)
        self.listen_state(self.change_solaire, self.entity_pac_climate)
        self.listen_state(self.change_solaire, self.entity_pompe_conso)
        if self.entity_pv_power:
            self.listen_state(self.change_solaire, self.entity_pv_power)

        self.listen_state(self.raz_temporisation_mesure_temp, self.args["cde_pompe"], new="off")

        if "arret_force" in self.args:
            self.listen_state(self.change_arret_force, self.args["arret_force"])

        if self.entity_volet_piscine:
            self.listen_state(self.change_electrolyseur_context, self.entity_volet_piscine)

        if self.entity_consigne_electrolyseur:
            self.listen_state(self.change_electrolyseur_context, self.args["cde_pompe"])
            self.listen_state(self.change_electrolyseur_context, self.args["fan_variateur_pompe"])

        if self.entity_consigne_electrolyseur_volet_ouvert:
            self.listen_state(self.change_electrolyseur_context, self.entity_consigne_electrolyseur_volet_ouvert)

        if self.entity_consigne_electrolyseur_volet_ferme:
            self.listen_state(self.change_electrolyseur_context, self.entity_consigne_electrolyseur_volet_ferme)

        self.listen_state(self.change_tempo_circulation_eau, self.args["tempo_eau"])
        self.listen_state(self.hors_gel_settings_changed, self.args["duree_bras_hors_gel"])
        self.listen_state(self.hors_gel_settings_changed, self.args["intervalle_bras_hors_gel"])

        self.run_every(self.check_etats_speciaux, "now", 30)
        self.run_every(self.actualisation_periodique, "now", 300)

        # Ne jamais arrêter systématiquement la filtration au chargement de l'app.
        # L'état réel est repris puis la logique normale décide de la suite.
        self.maj_electrolyseur()
        self.set_debug_w("")

    def change_electrolyseur_context(self, entity, attribute, old, new, kwargs):
        self.maj_electrolyseur()

    def change_temp(self, entity, attribute, old, new, kwargs):
        if new != "unavailable":
            self.traitement(kwargs)

    def change_solaire(self, entity, attribute, old, new, kwargs):
        mode = self.get_state(self.args["mode_de_fonctionnement"]).strip()
        if mode == TAB_MODE[1]:
            self.traitement(kwargs)

    def change_mode(self, entity, attribute, old, new, kwargs):
        self.fin_tempo = 0
        self.cancel_pending_start_sequence()

        self.stabilisation_active = False
        self.fin_stabilisation = None
        self.stabilisation_deja_faite = False

        self.brassage_en_cours = False
        self.type_brassage = None
        self.fin_brassage = None

        self.lock_text = False
        self.mode_actif = None
        self.debut_manque_soleil = None
        self.reset_stabilite_surplus()
        self.reset_pid()
        self.maj_electrolyseur()

        if self.handle_bras is not None:
            try:
                self.cancel_timer(self.handle_bras)
            except Exception:
                pass
            self.handle_bras = None
            self.prochain_bras = None

        if new.strip() == TAB_MODE[2]:
            if not ("arret_force" in self.args and self.get_state(self.args["arret_force"]) == "on"):
                self.planifier_bras(0)

        self.traitement(kwargs)

    def change_coef(self, entity, attribute, old, new, kwargs):
        self.traitement(kwargs)

    def change_mode_calcul(self, entity, attribute, old, new, kwargs):
        self.traitement(kwargs)

    def hors_gel_settings_changed(self, entity, attribute, old, new, kwargs):
        mode = self.get_state(self.args["mode_de_fonctionnement"]).strip()
        if mode != TAB_MODE[2]:
            self.traitement(kwargs)
            return

        if "arret_force" in self.args and self.get_state(self.args["arret_force"]) == "on":
            return

        if self.brassage_en_cours:
            self.turn_off_pompe_mem()
        self.brassage_en_cours = False
        self.type_brassage = None
        self.fin_brassage = None

        if self.handle_bras is not None:
            try:
                self.cancel_timer(self.handle_bras)
            except Exception:
                pass
            self.handle_bras = None

        self.prochain_bras = None
        self.lock_text = False
        self.planifier_bras(0)

    def change_arret_force(self, entity, attribute, old, new, kwargs):
        if new == "on":
            self.turn_off_pompe_mem(force=True)
            self.stabilisation_active = False
            self.fin_stabilisation = None
            self.stabilisation_deja_faite = False
            self.brassage_en_cours = False
            self.type_brassage = None
            self.fin_brassage = None
            self.debut_manque_soleil = None
            self.reset_stabilite_surplus()
            self.reset_pid()

            if self.handle_bras is not None:
                try:
                    self.cancel_timer(self.handle_bras)
                except Exception:
                    pass
                self.handle_bras = None
                self.prochain_bras = None

            self.lock_text = True
            self.set_messages("Arrêt forcé", "")
            self.set_debug_w("")

        elif new == "off":
            self.lock_text = False
            self.traitement(kwargs)

    def raz_temporisation_mesure_temp(self, entity, attribute, old, new, kwargs):
        self.fin_tempo = 0

    def ecretage_h_pivot(self, entity, attribute, old, new, kwargs):
        if new > "15:00:00":
            self.set_state(self.args["h_pivot"], state="15:00:00")
        elif new < "10:00:00":
            self.set_state(self.args["h_pivot"], state="10:00:00")
        self.traitement(kwargs)

    def change_tempo_circulation_eau(self, entity, attribute, old, new, kwargs):
        self.fin_tempo = 0
        self.cancel_pending_start_sequence()

        self.stabilisation_active = False
        self.fin_stabilisation = None
        self.stabilisation_deja_faite = False
        self.brassage_en_cours = False
        self.type_brassage = None
        self.fin_brassage = None
        self.debut_manque_soleil = None
        self.reset_stabilite_surplus()
        self.reset_pid()

        if self.handle_bras is not None:
            try:
                self.cancel_timer(self.handle_bras)
            except Exception:
                pass
            self.handle_bras = None
            self.prochain_bras = None

        self.lock_text = False
        self.mode_actif = None
        self.traitement(kwargs)

    def check_etats_speciaux(self, kwargs):
        now = datetime.datetime.now()

        self.maj_cumul_filtration()

        if self.arret_force_actif():
            if self.pompe_est_on() or self.stop_sequence_is_running():
                self.turn_off_pompe_mem(force=True)
            if not self.lock_text:
                self.set_messages("Arrêt forcé", "")
                self.set_debug_w("")
            return

        if self.start_sequence_is_running() or self.stop_sequence_is_running():
            return

        if self.stabilisation_active and self.fin_stabilisation and now >= self.fin_stabilisation:
            self.stabilisation_active = False
            self.fin_stabilisation = None
            self.stabilisation_deja_faite = True
            self.traitement({})

        if self.brassage_en_cours and self.fin_brassage and now >= self.fin_brassage:
            self.brassage_en_cours = False
            self.type_brassage = None
            self.fin_brassage = None
            self.turn_off_pompe_mem()

            mode = self.get_state(self.args["mode_de_fonctionnement"]).strip()
            self.lock_text = False
            if mode == TAB_MODE[2]:
                self.afficher_prochain_bras()
            else:
                self.traitement({})
            return

        if self.pompe_est_on():
            tempo_eau = int(float(self.get_state(self.args["tempo_eau"])))
            if self.temps_depuis_on() >= tempo_eau:
                self.fin_tempo = 1

    def actualisation_periodique(self, kwargs):
        self.traitement({})

    def planifier_bras(self, delay_sec):
        if self.handle_bras is not None:
            try:
                self.cancel_timer(self.handle_bras)
            except Exception:
                pass
            self.handle_bras = None

        when = datetime.datetime.now() + timedelta(seconds=delay_sec)
        self.prochain_bras = when
        self.handle_bras = self.run_in(self.rebrassage_hors_gel, delay_sec)

    def afficher_prochain_bras(self):
        self.set_debug_w("")
        if self.prochain_bras and not self.lock_text:
            self.set_messages(f"Hors gel | {self.prochain_bras.strftime('%H:%M')}", self.prochain_bras.strftime('%H:%M'))

    def rebrassage_hors_gel(self, kwargs):
        mode = self.get_state(self.args["mode_de_fonctionnement"]).strip()
        if mode != TAB_MODE[2]:
            self.handle_bras = None
            self.prochain_bras = None
            return

        if "arret_force" in self.args and self.get_state(self.args["arret_force"]) == "on":
            return

        maintenant = datetime.datetime.now()
        duree_bras = float(self.get_state(self.args["duree_bras_hors_gel"]))
        intervalle_bras = float(self.get_state(self.args["intervalle_bras_hors_gel"]))

        if not self.pompe_est_on():
            self.start_pump_with_delayed_speed(self.vitesse_min_filtration_utile, delay_s=2, context="hors_gel")
        else:
            self.set_pump_percentage(self.vitesse_min_filtration_utile, force=True)

        self.brassage_en_cours = True
        self.type_brassage = "hors_gel"
        self.fin_brassage = maintenant + timedelta(minutes=duree_bras)

        self.lock_text = True
        short_msg = f"Hors gel | {maintenant.strftime('%H:%M')}-{(maintenant + timedelta(minutes=duree_bras)).strftime('%H:%M')}"
        detail_msg = f"{self.vitesse_min_filtration_utile}% | {maintenant.strftime('%H:%M')}-{(maintenant + timedelta(minutes=duree_bras)).strftime('%H:%M')}"
        self.set_messages(short_msg, detail_msg)
        self.set_debug_w("")

        delay_sec = int((duree_bras + intervalle_bras) * 60)
        self.planifier_bras(delay_sec)

    def progression_attendue(self, objectif_temps_eq):
        if objectif_temps_eq <= 0:
            return 0.0

        debut = heure_to_timedelta(self.heure_debut_solaire)
        fin = heure_to_timedelta(self.heure_fin_solaire)
        maintenant = self.td_now()

        if maintenant <= debut:
            return 0.0
        if maintenant >= fin:
            return round(objectif_temps_eq, 3)

        duree_totale_s = (fin - debut).total_seconds()
        duree_ecoulee_s = (maintenant - debut).total_seconds()
        if duree_totale_s <= 0:
            return 0.0

        ratio = duree_ecoulee_s / duree_totale_s
        return round(objectif_temps_eq * ratio, 3)

    def limite_vitesse_surplus_fragile(self, vitesse, pac_chauffe=False, pac_prioritaire=False, rattrapage=False):
        if vitesse is None:
            return None

        plafond = self.vitesse_max_maintien_faible_pac if (pac_chauffe or pac_prioritaire) else self.vitesse_max_maintien_faible
        if rattrapage:
            plafond = min(plafond, self.vitesse_max_rattrapage_surplus_faible)

        return int(max(self.vitesse_min_filtration_utile, min(plafond, vitesse)))

    def set_textvalue(self, entity_id, value):
        value = str(value)
        if entity_id == self.args.get("message_filtration_piscine"):
            if self.last_message_filtration == value:
                return
            self.last_message_filtration = value

        try:
            self.call_service("input_text/set_value", entity_id=entity_id, value=value)
        except Exception:
            pass

    def set_detail_text(self, value):
        if not self.entity_message_detail:
            return

        value = str(value)
        if self.last_message_detail == value:
            return

        self.last_message_detail = value
        try:
            self.call_service("input_text/set_value", entity_id=self.entity_message_detail, value=value)
        except Exception:
            pass

    def set_messages(self, short_msg, detail_msg=""):
        self.set_textvalue(self.args["message_filtration_piscine"], short_msg)
        self.set_detail_text(detail_msg)

    def set_value(self, entity_id, value):
        try:
            self.call_service("input_number/set_value", entity_id=entity_id, value=value)
        except Exception:
            pass

    def set_debug_w(self, texte):
        if not self.entity_debug_w:
            return
        texte = str(texte)
        if self.last_debug_message == texte:
            return
        self.last_debug_message = texte
        try:
            self.call_service("input_text/set_value", entity_id=self.entity_debug_w, value=texte)
        except Exception:
            pass

    def notification(self, message, niveau):
        if niveau <= JOURNAL:
            self.log(f"{datetime.datetime.now().strftime('%H:%M:%S')}: {message}", log="piscine_log")
