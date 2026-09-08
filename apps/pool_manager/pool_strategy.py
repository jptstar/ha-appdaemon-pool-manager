# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import datetime
from datetime import timedelta

from pool_common import *

class StrategyMixin:

    def calcule_vitesse_rattrapage(self, retard, objectif_temps_eq):
        if objectif_temps_eq <= 0:
            return None

        ratio = retard / max(objectif_temps_eq, 0.01)

        if ratio < self.seuil_ratio_rattrapage_1:
            return None
        elif ratio < self.seuil_ratio_rattrapage_2:
            return max(self.vitesse_min_filtration_utile, self.vitesse_min_rattrapage)
        elif ratio < self.seuil_ratio_rattrapage_3:
            return max(self.vitesse_min_filtration_utile, self.vitesse_rattrapage_palier_2)
        elif ratio < self.seuil_ratio_rattrapage_4:
            return max(self.vitesse_min_filtration_utile, self.vitesse_rattrapage_palier_3)
        else:
            return max(self.vitesse_min_filtration_utile, self.vitesse_rattrapage_palier_4)

    def temps_restant_plage_solaire_h(self):
        now_td = self.td_now()
        fin = heure_to_timedelta(self.heure_fin_solaire)
        if now_td >= fin:
            return 0.0
        return max(0.0, (fin - now_td).total_seconds() / 3600.0)

    def temps_restant_avant_limite_quota_h(self):
        now_td = self.td_now()
        limite_td = heure_to_timedelta(self.heure_limite_quota_journalier)

        if now_td >= limite_td:
            return 0.0

        return max(0.0, (limite_td - now_td).total_seconds() / 3600.0)

    def calcule_vitesse_quota_requise(self, filtre_temps_eq, objectif_temps_eq):
        if not self.garantir_quota_journalier:
            return None

        reste_temps_eq = max(0.0, objectif_temps_eq - filtre_temps_eq)
        if reste_temps_eq <= 0:
            return None

        temps_restant_h = self.temps_restant_avant_limite_quota_h()
        if temps_restant_h <= 0:
            return float("inf")

        vitesse_ref = max(self.vitesse_min_filtration_utile, self.vitesse_reference_filtration)
        return (reste_temps_eq / temps_restant_h) * vitesse_ref

    def calcule_vitesse_garantie_quota(self, filtre_temps_eq, objectif_temps_eq):
        vitesse_requise = self.calcule_vitesse_quota_requise(filtre_temps_eq, objectif_temps_eq)
        if vitesse_requise is None:
            return None
        if vitesse_requise == float("inf"):
            return self.vitesse_max_garantie_quota

        vitesse_necessaire = int(round(vitesse_requise))
        vitesse_necessaire = max(self.vitesse_min_garantie_quota, vitesse_necessaire)
        vitesse_necessaire = min(self.vitesse_max_garantie_quota, vitesse_necessaire)
        vitesse_necessaire = max(self.vitesse_min_filtration_utile, vitesse_necessaire)
        return vitesse_necessaire

    def etat_garantie_quota(self, filtre_temps_eq, objectif_temps_eq):
        """Describe whether quota catch-up should override energy optimisation.

        A daily target can become mathematically impossible before the configured
        deadline. In that case forcing 100% indefinitely only increases grid
        import without restoring the guarantee, so the controller falls back to
        the normal solar/PAC optimisation and reports the situation.
        """
        vitesse_requise = self.calcule_vitesse_quota_requise(filtre_temps_eq, objectif_temps_eq)
        vitesse = self.calcule_vitesse_garantie_quota(filtre_temps_eq, objectif_temps_eq)

        if vitesse_requise is None or vitesse is None:
            return {
                "critique": False,
                "recuperable": True,
                "vitesse": None,
                "vitesse_requise": None,
            }

        temps_restant_h = self.temps_restant_avant_limite_quota_h()
        if temps_restant_h <= 0 or vitesse_requise > self.vitesse_max_garantie_quota:
            return {
                "critique": False,
                "recuperable": False,
                "vitesse": vitesse,
                "vitesse_requise": vitesse_requise,
            }

        reste_temps_eq = max(0.0, objectif_temps_eq - filtre_temps_eq)
        vitesse_ref = max(self.vitesse_min_filtration_utile, self.vitesse_reference_filtration)
        capacite_max_eq = temps_restant_h * (self.vitesse_max_garantie_quota / vitesse_ref)
        reserve_eq_h = max(0.0, capacite_max_eq - reste_temps_eq)

        # Keep a small equivalent-filtration reserve before quota becomes a
        # hard priority. This lets solar/PID consume available PV first.
        marge_critique_eq_h = 0.50
        critique = reserve_eq_h <= marge_critique_eq_h

        return {
            "critique": critique,
            "recuperable": True,
            "vitesse": vitesse,
            "vitesse_requise": vitesse_requise,
        }

    def quota_en_retard_obligatoire(self, filtre_temps_eq, objectif_temps_eq):
        etat = self.etat_garantie_quota(filtre_temps_eq, objectif_temps_eq)
        if not etat["critique"]:
            return False, etat["vitesse"]
        return True, etat["vitesse"]

    def ajuste_vitesse_rattrapage_selon_fin_plage(self, vitesse, retard, objectif_temps_eq):
        if vitesse is None:
            return None

        temps_restant = self.temps_restant_plage_solaire_h()
        if temps_restant <= 0:
            return vitesse

        ratio_retard = retard / max(objectif_temps_eq, 0.01)

        if temps_restant < 1.5 and ratio_retard > self.seuil_ratio_rattrapage_2:
            vitesse += 10
        elif temps_restant < 3.0 and ratio_retard > self.seuil_ratio_rattrapage_3:
            vitesse += 5

        return int(max(self.vitesse_min_filtration_utile, min(self.vitesse_max_solaire, vitesse)))

    def autoriser_complement_soir(self, filtre_temps_eq, objectif_temps_eq, reste_temps_eq):
        if objectif_temps_eq <= 0:
            return False

        avancement = filtre_temps_eq / max(objectif_temps_eq, 0.01)

        if reste_temps_eq < self.reste_min_complement_soir_h:
            return False
        if avancement >= self.avancement_min_sans_complement:
            return False

        return True

    def format_texte_solaire_debug(self, vitesse, surplus_net=None, reseau_net=None, pv_power=None):
        debug_parts = []
        try:
            puissance, reelle = self.puissance_pompe_affichee(vitesse)
            if puissance is not None:
                suffixe = "réel" if reelle else "estimé"
                debug_parts.append(f"Pompe {int(round(puissance))}W {suffixe}")
            if surplus_net is not None:
                debug_parts.append(f"Surplus {int(round(surplus_net))}W")
            if reseau_net is not None:
                debug_parts.append(f"Réseau {int(round(reseau_net))}W")
            if pv_power is not None:
                debug_parts.append(f"PV {int(round(pv_power))}W")
        except Exception:
            pass
        self.set_debug_w(" | ".join(debug_parts))

    def stabilite_surplus_ok(self, surplus_disponible):
        now = datetime.datetime.now()
        condition_ok = surplus_disponible >= self.seuil_surplus_demarrage_w

        if condition_ok:
            if self.debut_stabilite_surplus is None:
                self.debut_stabilite_surplus = now
        else:
            self.debut_stabilite_surplus = None
            return False, self.tempo_stabilite_surplus

        ecoule = (now - self.debut_stabilite_surplus).total_seconds()
        restant = max(0, int(self.tempo_stabilite_surplus - ecoule))
        return ecoule >= self.tempo_stabilite_surplus, restant

    def reset_stabilite_surplus(self):
        self.debut_stabilite_surplus = None

    def set_debug_solaire_off(self, surplus_net, reseau_net, pv_power=None):
        debug_parts = [f"Réseau {int(round(reseau_net))}W"]
        if surplus_net > 0:
            debug_parts.insert(0, f"Surplus {int(round(surplus_net))}W")
        if pv_power is not None:
            debug_parts.append(f"PV {int(round(pv_power))}W")
        self.set_debug_w(" | ".join(debug_parts))

    def is_night_brassage_slot(self, heure_actuelle):
        # Conservé volontairement : brassage nuit possible toutes les heures.
        heure_entiere = heure_actuelle.hour
        return (heure_entiere % 1 == 0) and (0 <= heure_actuelle.minute < 10)

    def current_night_slot_key(self, now_dt):
        return f"{now_dt.date().isoformat()}_{now_dt.hour:02d}"

    def start_night_brassage(self, now_dt, filtre_temps_eq, objectif_temps_eq):
        slot_key = self.current_night_slot_key(now_dt)
        if self.last_night_slot == slot_key:
            return True

        self.last_night_slot = slot_key
        self.debut_manque_soleil = None

        if not self.pompe_est_on():
            self.start_pump_with_delayed_speed(self.vitesse_brassage_nuit, delay_s=2, context="brassage_nuit")
        else:
            self.set_pump_percentage(self.vitesse_brassage_nuit, force=True)

        self.brassage_en_cours = True
        self.type_brassage = "nuit"
        self.fin_brassage = now_dt + timedelta(minutes=20)
        self.lock_text = True

        short_msg = f"Brassage nuit | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
        detail_msg = f"{self.vitesse_brassage_nuit}% | {now_dt.hour:02d}:00-{now_dt.hour:02d}:20 | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
        self.set_messages(short_msg, detail_msg)
        self.set_debug_w("")
        return True

    def appliquer_priorite_pac_ou_quota(self, filtre_temps_eq, objectif_temps_eq, pompe_on, pac_besoin, pac_chauffe, etat_pac, surplus_net=None, reseau_net=None, pv_power=None, extra=""):
        etat_quota = self.etat_garantie_quota(filtre_temps_eq, objectif_temps_eq)
        quota_critique = etat_quota["critique"]
        quota_recuperable = etat_quota["recuperable"]
        vitesse_quota = etat_quota["vitesse"]
        vitesse_quota_requise = etat_quota["vitesse_requise"]

        if self.pac_prioritaire_absolue and pac_besoin:
            self.debut_manque_soleil = None
            self.reset_stabilite_surplus()
            self.reset_pid()

            vitesse_min_pac = max(
                self.vitesse_min_filtration_utile,
                self.vitesse_pac_prioritaire,
                self.vitesse_min_pac_active,
            )

            if quota_critique and vitesse_quota is not None:
                vitesse = max(vitesse_min_pac, vitesse_quota)
                libelle = "PAC prioritaire + quota critique"

                if not pompe_on:
                    self.start_pump_with_delayed_speed(vitesse, delay_s=2, context="pac_quota_critique")
                    self.set_messages(
                        f"{libelle} | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                        f"démarrage | {vitesse}% | limite {self.heure_limite_quota_journalier[:5]} | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                    )
                    self.set_debug_w(f"PAC {etat_pac}")
                    return True

                vitesse_appliquee = self.set_pump_percentage(vitesse)
                short_msg, detail_msg = self.split_message_solaire(
                    libelle,
                    vitesse_appliquee,
                    filtre_temps_eq,
                    objectif_temps_eq,
                    extra=f"limite {self.heure_limite_quota_journalier[:5]}"
                )
                self.set_messages(short_msg, detail_msg)
                self.format_texte_solaire_debug(vitesse_appliquee, surplus_net, reseau_net, pv_power)
                return True

            if not pompe_on:
                self.start_pump_with_delayed_speed(vitesse_min_pac, delay_s=2, context="pac_prioritaire")
                extra_quota = ""
                if not quota_recuperable:
                    extra_quota = f"quota impossible avant {self.heure_limite_quota_journalier[:5]}"
                self.set_messages(
                    f"PAC prioritaire | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                    f"démarrage | {vitesse_min_pac}% | {extra_quota} | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h".replace(" |  |", " |")
                )
                self.set_debug_w(f"PAC {etat_pac}")
                return True

            # During the configured solar window, let the downstream PID/solar
            # logic use exported power. As soon as grid import exceeds the PAC
            # allowance, hold the pump at its minimum PAC circulation speed.
            en_plage_solaire = self.est_dans_plage(self.heure_debut_solaire, self.heure_fin_solaire)
            import_reseau = max(0.0, float(reseau_net or 0.0))
            if en_plage_solaire and import_reseau <= self.pac_import_max_jour_w:
                return False

            vitesse_appliquee = self.set_pump_percentage(vitesse_min_pac)
            detail_extra = extra
            if not quota_recuperable:
                detail_extra = f"quota impossible avant {self.heure_limite_quota_journalier[:5]}"
            elif vitesse_quota_requise is not None and vitesse_quota_requise > vitesse_min_pac:
                detail_extra = f"quota différé | besoin {int(round(vitesse_quota_requise))}% maintenant"

            short_msg, detail_msg = self.split_message_solaire(
                "PAC éco réseau",
                vitesse_appliquee,
                filtre_temps_eq,
                objectif_temps_eq,
                extra=detail_extra
            )
            self.set_messages(short_msg, detail_msg)
            self.format_texte_solaire_debug(vitesse_appliquee, surplus_net, reseau_net, pv_power)
            return True

        if quota_critique and vitesse_quota is not None:
            self.debut_manque_soleil = None
            self.reset_stabilite_surplus()
            self.reset_pid()

            vitesse = max(self.vitesse_min_filtration_utile, vitesse_quota)
            if pac_chauffe:
                vitesse = max(vitesse, self.vitesse_min_pac_active)

            if not pompe_on:
                self.start_pump_with_delayed_speed(vitesse, delay_s=2, context="garantie_quota_critique")
                self.set_messages(
                    f"Garantie quota critique | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h",
                    f"démarrage | {vitesse}% | limite {self.heure_limite_quota_journalier[:5]} | {filtre_temps_eq:.1f}/{objectif_temps_eq:.1f}h"
                )
                self.set_debug_w("")
                return True

            vitesse_appliquee = self.set_pump_percentage(vitesse)
            short_msg, detail_msg = self.split_message_solaire(
                "Garantie quota critique",
                vitesse_appliquee,
                filtre_temps_eq,
                objectif_temps_eq,
                extra=f"limite {self.heure_limite_quota_journalier[:5]}"
            )
            self.set_messages(short_msg, detail_msg)
            self.format_texte_solaire_debug(vitesse_appliquee, surplus_net, reseau_net, pv_power)
            return True

        return False
