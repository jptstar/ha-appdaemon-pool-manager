# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import datetime
from datetime import timedelta

from pool_common import *

class ControlMixin:

    def traitement(self, kwargs):
        self.maj_cumul_filtration()

        mode = self.get_state(self.args["mode_de_fonctionnement"]).strip()
        self.mode_actif = mode
        self.sync_local_panel_policy(mode)

        if self.arret_force_actif():
            self.turn_off_pompe_mem(force=True)
            self.reset_stabilite_surplus()
            self.reset_pid()
            if not self.lock_text:
                self.set_messages("Arrêt forcé", "")
                self.set_debug_w("")
            return

        if self.start_sequence_is_running() or self.stop_sequence_is_running():
            return

        now_dt = datetime.datetime.now()
        heure_actuelle = now_dt.time()
        nuit = self.est_nuit(now_dt)
        h_maintenant = self.td_now()

        mesure_eau = self.get_float_state(self.args["temperature_eau"], 10.0)
        mem_eau = self.get_float_state(self.args["mem_temp"], mesure_eau)

        if self.fin_tempo == 1:
            temperature_eau = mesure_eau
            self.set_value(self.args["mem_temp"], mesure_eau)
        else:
            temperature_eau = mem_eau

        coef = self.get_float_state(self.args["coef"], 100.0) / 100.0
        pivot_txt = self.get_state(self.args["h_pivot"])
        mode_calcul = self.get_state(self.args["mode_calcul"])

        # Une journée ne peut jamais demander plus de 24 h équivalentes.
        # Le même plafond est utilisé pour l'affichage, le quota et le planning.
        temps_filtration = calcule_objectif_filtration(temperature_eau, coef, mode_calcul == "on")
        self.set_value(self.args["duree_filtration_ete"], round(temps_filtration, 2))

        objectif_temps_eq = temps_filtration
        filtre_temps_eq = self.temps_filtration_equivalent_jour

        h_pivot = heure_to_timedelta(pivot_txt)
        h_debut, h_fin = calcule_plage_filtration(temps_filtration, pivot_txt)

        texte_plage = f"{str(h_debut).zfill(8)[:5]}-{str(h_pivot).zfill(8)[:5]}-{str(h_fin).zfill(8)[:5]}"

        if self.stabilisation_active or self.brassage_en_cours:
            self.maj_electrolyseur()
            if not self.lock_text:
                self.set_messages(
                    f"En cours | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                    f"{texte_plage} | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                )
                self.set_debug_w("")
            return

        if mode == TAB_MODE[2]:
            self.reset_stabilite_surplus()
            self.reset_pid()
            self.maj_electrolyseur()
            if self.handle_bras is None:
                self.planifier_bras(0)
            self.afficher_prochain_bras()
            return

        if mode == TAB_MODE[3]:
            self.reset_stabilite_surplus()
            self.reset_pid()

            if not self.pompe_est_on():
                self.start_pump_with_delayed_speed(self.vitesse_min_filtration_utile, delay_s=2, context="marche_forcee")
                self.set_messages(
                    f"Marche forcée | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                    f"démarrage | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                )
                self.set_debug_w("")
                return

            vitesse_appliquee = self.get_fan_percentage()
            if vitesse_appliquee is None:
                vitesse_appliquee = self.derniere_vitesse_commande if self.derniere_vitesse_commande is not None else self.vitesse_min_filtration_utile

            debit = self.debit_pompe(vitesse_appliquee)
            self.set_messages(
                f"Marche forcée | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                f"{vitesse_appliquee}% | {debit:.1f}m3/h | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
            )

            puissance, reelle = self.puissance_pompe_affichee(vitesse_appliquee)
            if puissance is None:
                self.set_debug_w("")
            else:
                suffixe = "réel" if reelle else "estimé"
                self.set_debug_w(f"Pompe {int(round(puissance))}W {suffixe}")
            return

        if mode == TAB_MODE[0]:
            self.reset_stabilite_surplus()
            self.reset_pid()

            if nuit and not (h_debut <= h_maintenant <= h_fin):
                if self.is_night_brassage_slot(heure_actuelle):
                    self.start_night_brassage(now_dt, filtre_temps_eq, objectif_temps_eq)
                    return

            if h_debut <= h_maintenant <= h_fin:
                if (not self.stabilisation_active) and (not self.stabilisation_deja_faite):
                    stabilisation_max = timedelta(hours=1)
                    temps_restant = h_fin - h_maintenant
                    duree_stab = min(stabilisation_max, temps_restant)

                    if duree_stab.total_seconds() > 0:
                        if not self.pompe_est_on():
                            self.start_pump_with_delayed_speed(self.vitesse_mode_temperature, delay_s=2, context="stabilisation_temperature")
                        elif not self.mode_speed_initialized:
                            self.set_pump_percentage(self.vitesse_mode_temperature, force=True)
                            self.mode_speed_initialized = True

                        self.stabilisation_active = True
                        self.fin_stabilisation = now_dt + duree_stab

                        debit = self.debit_pompe(self.vitesse_mode_temperature)
                        self.set_messages(
                            f"Stabilisation | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                            f"{int(self.vitesse_mode_temperature)}% | {debit:.1f}m3/h | fin {self.fin_stabilisation.strftime('%H:%M')} | {texte_plage} | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                        )
                        self.format_texte_solaire_debug(self.vitesse_mode_temperature)
                        return

                if not self.pompe_est_on():
                    self.start_pump_with_delayed_speed(self.vitesse_mode_temperature, delay_s=2, context="temperature")
                    self.set_messages(
                        f"Température | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                        f"démarrage | {texte_plage} | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                    )
                    self.set_debug_w("")
                    return

                if not self.mode_speed_initialized:
                    vitesse_appliquee = self.set_pump_percentage(self.vitesse_mode_temperature, force=True)
                    self.mode_speed_initialized = True
                else:
                    vitesse_appliquee = self.get_fan_percentage()
                    if vitesse_appliquee is None:
                        vitesse_appliquee = self.derniere_vitesse_commande if self.derniere_vitesse_commande is not None else self.vitesse_mode_temperature

                debit = self.debit_pompe(vitesse_appliquee)
                self.set_messages(
                    f"Température | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                    f"{vitesse_appliquee}% | {debit:.1f}m3/h | {texte_plage} | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                )
                self.format_texte_solaire_debug(vitesse_appliquee)
                return

            self.turn_off_pompe_mem()
            self.set_messages(
                f"Température | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                f"{texte_plage} | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
            )
            self.set_debug_w("")
            return

        if mode == TAB_MODE[1]:
            reseau_net = self.get_reseau_net_w()
            surplus_brut = self.get_surplus_depuis_reseau_net()
            surplus_net, etat_pac, _ = self.calcule_surplus_net_avec_pac(surplus_brut)
            pv_power = self.get_pv_power()

            pac_prioritaire_jour = self.pac_mode_prioritaire_soleil()
            surplus_pilote = self.surplus_equivalent_pac_jour(surplus_net)
            tempo_anti_coupure_active = self.tempo_anti_coupure_active()

            reste_temps_eq = max(0.0, objectif_temps_eq - filtre_temps_eq)
            attendu = self.progression_attendue(objectif_temps_eq)
            retard = max(0.0, attendu - filtre_temps_eq)

            en_plage_solaire = self.est_dans_plage(self.heure_debut_solaire, self.heure_fin_solaire)
            en_plage_rattrapage_soir = self.est_dans_plage(self.heure_fin_solaire, self.heure_fin_rattrapage)
            encore_dans_plage_temperature = h_debut <= h_maintenant <= h_fin

            pompe_on = self.pompe_est_on()
            pac_ok = self.pac_autorisee() is True
            pac_chauffe = pac_ok and etat_pac in ["silent", "smart", "turbo"]
            pac_besoin = self.pac_besoin_chauffe()

            # PRIORITÉ ABSOLUE : PAC ou quota journalier, même la nuit.
            if nuit:
                self.reset_stabilite_surplus()
                self.reset_pid()
                self.debut_manque_soleil = None

                if self.appliquer_priorite_pac_ou_quota(
                    filtre_temps_eq,
                    objectif_temps_eq,
                    pompe_on,
                    pac_besoin,
                    pac_chauffe,
                    etat_pac,
                    surplus_net,
                    reseau_net,
                    pv_power,
                    extra="nuit"
                ):
                    return

                if self.is_night_brassage_slot(heure_actuelle):
                    self.start_night_brassage(now_dt, filtre_temps_eq, objectif_temps_eq)
                    return

                self.turn_off_pompe_mem()
                self.set_messages(
                    f"Nuit attente | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                    f"{filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                )
                self.set_debug_w("")
                return

            if objectif_temps_eq <= 0:
                self.turn_off_pompe_mem()
                self.debut_manque_soleil = None
                self.reset_stabilite_surplus()
                self.reset_pid()
                short_msg, detail_msg = self.split_message_simple("Aucune filtration", filtre_temps_eq, objectif_temps_eq)
                self.set_messages(short_msg, detail_msg)
                self.set_debug_w("")
                return

            if reste_temps_eq <= 0:
                # Si le quota est atteint mais que la PAC a encore besoin de chauffer, elle garde la priorité.
                if self.pac_prioritaire_absolue and pac_besoin:
                    if self.appliquer_priorite_pac_ou_quota(
                        filtre_temps_eq,
                        objectif_temps_eq,
                        pompe_on,
                        pac_besoin,
                        pac_chauffe,
                        etat_pac,
                        surplus_net,
                        reseau_net,
                        pv_power
                    ):
                        return

                self.turn_off_pompe_mem()
                self.debut_manque_soleil = None
                self.reset_stabilite_surplus()
                self.reset_pid()
                short_msg, detail_msg = self.split_message_simple("Objectif atteint", filtre_temps_eq, objectif_temps_eq)
                self.set_messages(short_msg, detail_msg)
                self.set_debug_w("")
                return

            # Priorité PAC puis garantie quota avant l'optimisation solaire.
            if self.appliquer_priorite_pac_ou_quota(
                filtre_temps_eq,
                objectif_temps_eq,
                pompe_on,
                pac_besoin,
                pac_chauffe,
                etat_pac,
                surplus_net,
                reseau_net,
                pv_power
            ):
                return

            soleil_suffisant_pour_demarrer = surplus_pilote >= self.seuil_surplus_demarrage_w
            soleil_trop_faible_pour_rester = surplus_pilote < self.seuil_surplus_arret_w
            surplus_fragile = surplus_pilote < self.seuil_surplus_demarrage_w

            vitesse_rattrapage_dyn = self.calcule_vitesse_rattrapage(retard, objectif_temps_eq)
            vitesse_rattrapage_dyn = self.ajuste_vitesse_rattrapage_selon_fin_plage(vitesse_rattrapage_dyn, retard, objectif_temps_eq)

            if surplus_fragile and vitesse_rattrapage_dyn is not None:
                vitesse_rattrapage_dyn = self.limite_vitesse_surplus_fragile(
                    vitesse_rattrapage_dyn,
                    pac_chauffe=pac_chauffe,
                    pac_prioritaire=pac_prioritaire_jour,
                    rattrapage=True
                )

            complement_soir_autorise = self.autoriser_complement_soir(filtre_temps_eq, objectif_temps_eq, reste_temps_eq)

            if not pompe_on:
                self.debut_manque_soleil = None
                self.reset_pid()

                stable_surplus_ok, restant_stabilite = self.stabilite_surplus_ok(surplus_pilote)

                if soleil_suffisant_pour_demarrer and stable_surplus_ok and self.temps_depuis_off() >= self.tempo_min_off_solaire:
                    vitesse = max(self.calcule_vitesse_solaire(surplus_pilote), self.vitesse_min_filtration_utile)
                    if pac_chauffe or pac_prioritaire_jour:
                        vitesse = max(vitesse, self.vitesse_min_pac_active)

                    self.start_pump_with_delayed_speed(vitesse, delay_s=2, context="surplus_solaire")
                    short_msg, detail_msg = self.split_message_solaire(
                        "PAC + soleil" if pac_prioritaire_jour else "Surplus solaire",
                        vitesse,
                        filtre_temps_eq,
                        objectif_temps_eq
                    )
                    self.set_messages(short_msg, detail_msg)
                    self.format_texte_solaire_debug(vitesse, surplus_net, reseau_net, pv_power)
                    return

                if en_plage_solaire and vitesse_rattrapage_dyn is not None and stable_surplus_ok and self.temps_depuis_off() >= self.tempo_min_off_solaire:
                    vitesse = max(vitesse_rattrapage_dyn, self.vitesse_min_filtration_utile)
                    if surplus_net > 0:
                        vitesse = max(vitesse, self.vitesse_solaire_maintien(surplus_net))
                    if pac_chauffe or pac_prioritaire_jour:
                        vitesse = max(vitesse, self.vitesse_min_pac_active)
                    if surplus_fragile:
                        vitesse = self.limite_vitesse_surplus_fragile(vitesse, pac_chauffe=pac_chauffe, pac_prioritaire=pac_prioritaire_jour, rattrapage=True)

                    self.start_pump_with_delayed_speed(vitesse, delay_s=2, context="rattrapage")
                    libelle = "PAC + rattrapage" if pac_prioritaire_jour else ("Surplus + rattrapage" if surplus_net > 0 else "Rattrapage retard")
                    short_msg, detail_msg = self.split_message_solaire(libelle, vitesse, filtre_temps_eq, objectif_temps_eq)
                    self.set_messages(short_msg, detail_msg)
                    self.format_texte_solaire_debug(vitesse, surplus_net, reseau_net, pv_power)
                    return

                if en_plage_rattrapage_soir and complement_soir_autorise and self.temps_depuis_off() >= self.tempo_min_off_solaire:
                    self.reset_stabilite_surplus()
                    vitesse = min(self.vitesse_rattrapage, self.vitesse_max_complement_soir)
                    vitesse = max(vitesse, self.vitesse_min_filtration_utile)
                    if pac_chauffe:
                        vitesse = max(vitesse, self.vitesse_min_pac_active)

                    self.start_pump_with_delayed_speed(vitesse, delay_s=2, context="complement_soir")
                    short_msg, detail_msg = self.split_message_solaire("Complément soir", vitesse, filtre_temps_eq, objectif_temps_eq)
                    self.set_messages(short_msg, detail_msg)
                    self.format_texte_solaire_debug(vitesse, surplus_net, reseau_net, pv_power)
                    return

                if soleil_suffisant_pour_demarrer and not stable_surplus_ok:
                    self.set_messages(
                        f"Surplus faible | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                        f"attente on {max(0, int(self.tempo_min_off_solaire - self.temps_depuis_off()))}s | stabilité {restant_stabilite}s | surplus {int(round(max(0.0, surplus_pilote)))}W | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                    )
                elif surplus_brut <= 0:
                    self.reset_stabilite_surplus()
                    self.set_messages(
                        f"Conso maison forte | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                        f"{filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                    )
                elif surplus_pilote < self.seuil_surplus_demarrage_w:
                    self.reset_stabilite_surplus()
                    self.set_messages(
                        f"{'PAC consomme surplus' if pac_chauffe else 'Surplus insuffisant'} | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                        f"{filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                    )
                else:
                    self.set_messages(
                        f"Surplus OK | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                        f"redémarrage dans {max(0, int(self.tempo_min_off_solaire - self.temps_depuis_off()))}s | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                    )

                self.set_debug_solaire_off(surplus_net, reseau_net, pv_power)
                return

            if en_plage_rattrapage_soir and complement_soir_autorise and not en_plage_solaire:
                self.debut_manque_soleil = None
                self.reset_stabilite_surplus()
                self.reset_pid()

                vitesse = min(self.vitesse_rattrapage, self.vitesse_max_complement_soir)
                vitesse = max(vitesse, self.vitesse_min_filtration_utile)
                if pac_chauffe:
                    vitesse = max(vitesse, self.vitesse_min_pac_active)

                vitesse_appliquee = self.set_pump_percentage(vitesse)
                short_msg, detail_msg = self.split_message_solaire("Complément soir", vitesse_appliquee, filtre_temps_eq, objectif_temps_eq)
                self.set_messages(short_msg, detail_msg)
                self.format_texte_solaire_debug(vitesse_appliquee, surplus_net, reseau_net, pv_power)
                return

            if en_plage_solaire:
                self.reset_stabilite_surplus()
                vitesse_actuelle = self.get_fan_percentage()
                if vitesse_actuelle is None:
                    vitesse_actuelle = self.derniere_vitesse_commande if self.derniere_vitesse_commande is not None else self.vitesse_min_filtration_utile

                if soleil_trop_faible_pour_rester and self.temps_depuis_on() < self.tempo_min_on_solaire:
                    self.debut_manque_soleil = None
                    self.reset_pid()

                    vitesse = self.get_fan_percentage()
                    if vitesse is None:
                        vitesse = self.derniere_vitesse_commande if self.derniere_vitesse_commande is not None else self.vitesse_min_filtration_utile

                    self.set_messages(
                        f"Surplus faible | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                        f"arrêt dans {max(0, int(self.tempo_min_on_solaire - self.temps_depuis_on()))}s | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                    )
                    self.format_texte_solaire_debug(vitesse, surplus_net, reseau_net, pv_power)
                    return

                if soleil_trop_faible_pour_rester and self.temps_depuis_on() >= self.tempo_min_on_solaire:
                    if self.debut_manque_soleil is None:
                        self.debut_manque_soleil = datetime.datetime.now()

                    duree_manque = (datetime.datetime.now() - self.debut_manque_soleil).total_seconds()

                    if pac_prioritaire_jour and self.pac_maintien_jour_force:
                        self.reset_pid()
                        vitesse = max(self.vitesse_min_filtration_utile, self.vitesse_min_pac_active)
                        vitesse = self.limite_vitesse_surplus_fragile(vitesse, pac_chauffe=True, pac_prioritaire=True, rattrapage=False)
                        vitesse_appliquee = self.set_pump_percentage(vitesse)
                        short_msg, detail_msg = self.split_message_solaire("PAC prioritaire soleil", vitesse_appliquee, filtre_temps_eq, objectif_temps_eq, extra=f"maintien {int(duree_manque)}s")
                        self.set_messages(short_msg, detail_msg)
                        self.format_texte_solaire_debug(vitesse_appliquee, surplus_net, reseau_net, pv_power)
                        return

                    if pac_chauffe:
                        self.reset_pid()
                        vitesse = max(self.vitesse_min_filtration_utile, self.vitesse_min_pac_active)
                        vitesse = self.limite_vitesse_surplus_fragile(vitesse, pac_chauffe=True, pac_prioritaire=False, rattrapage=False)
                        vitesse_appliquee = self.set_pump_percentage(vitesse)
                        short_msg, detail_msg = self.split_message_solaire("Maintien pour PAC", vitesse_appliquee, filtre_temps_eq, objectif_temps_eq, extra=f"anti-coupure {int(duree_manque)}s")
                        self.set_messages(short_msg, detail_msg)
                        self.format_texte_solaire_debug(vitesse_appliquee, surplus_net, reseau_net, pv_power)
                        return

                    if duree_manque >= tempo_anti_coupure_active:
                        self.turn_off_pompe_mem()
                        self.debut_manque_soleil = None
                        self.reset_pid()
                        self.set_messages(
                            f"{'Conso maison forte' if surplus_brut <= 0 else 'Surplus insuffisant'} | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                            f"{filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                        )
                        self.set_debug_solaire_off(surplus_net, reseau_net, pv_power)
                        return

                    vitesse_pid, _, _ = self.calcule_vitesse_pid_hybride(reseau_net, vitesse_actuelle)
                    if vitesse_rattrapage_dyn is not None and not surplus_fragile:
                        vitesse_pid = max(vitesse_pid, vitesse_rattrapage_dyn)

                    vitesse_pid = self.limite_vitesse_surplus_fragile(vitesse_pid, pac_chauffe=False, pac_prioritaire=False, rattrapage=bool(vitesse_rattrapage_dyn is not None))
                    vitesse_appliquee = self.set_pump_percentage(vitesse_pid)
                    libelle = "Maintien + rattrapage" if vitesse_rattrapage_dyn is not None and not surplus_fragile else "Maintien surplus faible"
                    short_msg, detail_msg = self.split_message_solaire(libelle, vitesse_appliquee, filtre_temps_eq, objectif_temps_eq, extra=f"anti-coupure {int(duree_manque)}s")
                    self.set_messages(short_msg, detail_msg)
                    self.format_texte_solaire_debug(vitesse_appliquee, surplus_net, reseau_net, pv_power)
                    return

                self.debut_manque_soleil = None
                vitesse_pid, _, _ = self.calcule_vitesse_pid_hybride(reseau_net, vitesse_actuelle)

                if vitesse_rattrapage_dyn is not None and not surplus_fragile:
                    vitesse_pid = max(vitesse_pid, vitesse_rattrapage_dyn)
                elif vitesse_rattrapage_dyn is not None and surplus_fragile:
                    vitesse_pid = max(vitesse_pid, min(vitesse_rattrapage_dyn, self.vitesse_max_rattrapage_surplus_faible))

                if pac_chauffe or pac_prioritaire_jour:
                    vitesse_pid = max(vitesse_pid, self.vitesse_min_pac_active)

                if surplus_fragile:
                    vitesse_pid = self.limite_vitesse_surplus_fragile(vitesse_pid, pac_chauffe=pac_chauffe, pac_prioritaire=pac_prioritaire_jour, rattrapage=bool(vitesse_rattrapage_dyn is not None))
                else:
                    vitesse_pid = max(self.vitesse_min_filtration_utile, min(self.vitesse_max_solaire, vitesse_pid))

                vitesse_appliquee = self.set_pump_percentage(vitesse_pid)

                if vitesse_rattrapage_dyn is not None and pac_prioritaire_jour and not surplus_fragile:
                    libelle = "PAC + rattrapage"
                elif vitesse_rattrapage_dyn is not None and surplus_net > 0 and not surplus_fragile:
                    libelle = "Surplus + rattrapage"
                elif vitesse_rattrapage_dyn is not None and not surplus_fragile:
                    libelle = "Rattrapage retard"
                elif pac_prioritaire_jour:
                    libelle = "PAC + soleil"
                else:
                    libelle = "Surplus solaire"

                short_msg, detail_msg = self.split_message_solaire(libelle, vitesse_appliquee, filtre_temps_eq, objectif_temps_eq)
                self.set_messages(short_msg, detail_msg)
                self.format_texte_solaire_debug(vitesse_appliquee, surplus_net, reseau_net, pv_power)
                return

            self.debut_manque_soleil = None
            self.reset_stabilite_surplus()
            self.reset_pid()

            vitesse = self.vitesse_min_filtration_utile
            if pac_chauffe:
                vitesse = max(vitesse, self.vitesse_min_pac_active)

            vitesse_appliquee = self.set_pump_percentage(vitesse)
            short_msg, detail_msg = self.split_message_solaire("Maintien vitesse mini", vitesse_appliquee, filtre_temps_eq, objectif_temps_eq)
            self.set_messages(short_msg, detail_msg)
            self.format_texte_solaire_debug(vitesse_appliquee, surplus_net, reseau_net, pv_power)
            return

        self.set_messages(
            f"Mode inconnu | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
            f"{mode} | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
        )
        self.set_debug_w("")
