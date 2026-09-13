from pathlib import Path
import re


PATH = Path("apps/pool_manager/pool_heating.py")
text = PATH.read_text(encoding="utf-8")


def replace_once(old, new, label):
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    text = text.replace(old, new, 1)


def regex_once(pattern, replacement, label):
    global text
    text_new, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match, got {count}")
    text = text_new


replace_once(
    "from pool_common import TAB_MODE\nfrom pool_end_season import (\n"
    "    build_end_season_plan,\n"
    "    extract_weather_forecast,\n"
    "    normalize_daily_forecast,\n"
    ")\n",
    "from pool_common import TAB_MODE\n"
    "from pool_predictive_runtime import PredictiveHeatingSupport\n",
    "predictive import",
)

replace_once(
    'CHAUFFAGE_PREMIERE_CHAUFFE = "Première chauffe • Smart"\n'
    'CHAUFFAGE_FIN_SAISON = "Fin de saison • Smart"\n',
    'CHAUFFAGE_DEBUT_SAISON = "Début de saison • Smart"\n'
    '# Compatibility alias accepted when an existing HA selector still uses v0.5 wording.\n'
    'CHAUFFAGE_PREMIERE_CHAUFFE = "Première chauffe • Smart"\n'
    'CHAUFFAGE_FIN_SAISON = "Fin de saison • Smart"\n',
    "season-start constants",
)

replace_once(
    "    CHAUFFAGE_AUTO,\n    CHAUFFAGE_PREMIERE_CHAUFFE,\n    CHAUFFAGE_FIN_SAISON,\n",
    "    CHAUFFAGE_AUTO,\n    CHAUFFAGE_DEBUT_SAISON,\n"
    "    CHAUFFAGE_PREMIERE_CHAUFFE,\n    CHAUFFAGE_FIN_SAISON,\n",
    "mode set",
)

replace_once(
    '    if value == CHAUFFAGE_PREMIERE_CHAUFFE:\n        return "first_heat"\n',
    '    if value in {CHAUFFAGE_DEBUT_SAISON, CHAUFFAGE_PREMIERE_CHAUFFE}:\n'
    '        return "season_start"\n',
    "mode classification",
)

replace_once(
    "class HeatingModeMixin:\n",
    "class HeatingModeMixin(PredictiveHeatingSupport):\n",
    "support inheritance",
)

replace_once(
    "    first heat, end-of-season Smart maintenance and temporary Turbo modes.\n",
    "    season start, predictive automatic/end-of-season policy and Turbo modes.\n",
    "class docstring",
)

regex_once(
    r"\n        # Predictive end-of-season planner is opt-in for backward compatibility\..*?"
    r"\n\n        self\.chauffage_mode_precedent = CHAUFFAGE_AUTO",
    "\n        # v0.6 common predictive engine; v0.5 fin_saison_* keys remain aliases.\n"
    "        self._initialize_predictive_heating()\n\n"
    "        self.chauffage_mode_precedent = CHAUFFAGE_AUTO",
    "replace v0.5 predictive initialization",
)

regex_once(
    r"\n        self\.fin_saison_forecast = \[\].*?self\.fin_saison_last_log_signature = None\n",
    "\n",
    "remove old end-season state",
)

replace_once(
    '            "first_heat",\n            "end_season",\n            "turbo",\n',
    '            "season_start",\n            "end_season",\n            "turbo",\n',
    "explicit mode kinds",
)

regex_once(
    r"\n        if new_kind == \"end_season\" and self\.fin_saison_predictif:.*?"
    r"self\.fin_saison_last_plan = None\n",
    "\n        if self.chauffage_predictif and new_kind in {\"auto\", \"end_season\", \"season_start\"}:\n"
    "            # Re-evaluate weather immediately after a strategy change.\n"
    "            self.chauffage_predictif_forecast_at = None\n"
    "            self.chauffage_predictif_last_log_signature = None\n"
    "        if old_kind in {\"auto\", \"end_season\"} and old_kind != new_kind:\n"
    "            self.chauffage_predictif_heat_requested = False\n"
    "            self.chauffage_predictif_heat_target_c = None\n"
    "            self.chauffage_predictif_last_plan = None\n",
    "mode-change predictive reset",
)

replace_once(
    "        if not self.chauffage_mode_explicite() or not self._chauffage_pool_mode_autorise():\n"
    "            self._cancel_chauffage_start()\n"
    "            return\n",
    "        kind = chauffage_mode_kind(self.chauffage_mode())\n"
    "        if (\n"
    "            not self._predictive_start_still_allowed(kind)\n"
    "            or not self._chauffage_pool_mode_autorise()\n"
    "        ):\n"
    "            self._cancel_chauffage_start()\n"
    "            return\n",
    "pending-start mode guard",
)

replace_once(
    "    def _manage_premiere_chauffe(self):\n"
    "        water = self._premiere_chauffe_temperature()\n",
    "    def _manage_debut_saison(self):\n"
    "        self._update_predictive_diagnostics(\n"
    "            \"season_start\", override=\"🌡️ Début de saison\"\n"
    "        )\n"
    "        water = self._premiere_chauffe_temperature()\n",
    "rename season start handler",
)

replace_once(
    '                f"Première chauffe terminée ({water:.1f}/{target:.1f} °C) -> Automatique",\n',
    '                f"Début de saison terminé ({water:.1f}/{target:.1f} °C) -> Automatique",\n',
    "season start completion log",
)
replace_once(
    '            self._fault("chauffage_temperature", "température/consigne PAC indisponible pour fin de première chauffe")\n',
    '            self._fault("chauffage_temperature", "température/consigne PAC indisponible pour fin de début de saison")\n',
    "season start fault",
)
replace_once(
    '        self._request_chauffage_start(self.chauffage_preset_smart, "première chauffe")\n',
    '        self._request_chauffage_start(self.chauffage_preset_smart, "début de saison")\n',
    "season start label",
)

regex_once(
    r"\n    def _fin_saison_forecast_age_s\(self\):.*?"
    r"\n    def _manage_pac_auto\(self\):",
    "\n    def _manage_pac_auto(self):",
    "remove v0.5 end-season runtime",
)

regex_once(
    r"    def _manage_pac_auto\(self\):.*?\n    def pac_besoin_chauffe\(self\):.*?return super\(\)\.pac_besoin_chauffe\(\)\n?$",
    '''    def _manage_pac_auto(self):
        # No selector configured: strict pre-v0.4.4 compatibility.
        if not self.entity_chauffage:
            return super()._manage_pac_auto()

        self._update_predictive_learning()
        mode_chauffage = self.chauffage_mode()
        kind = chauffage_mode_kind(mode_chauffage)

        if kind == "unknown":
            self._cancel_chauffage_start()
            self._fault("chauffage_mode", "mode chauffage indisponible/inconnu")
            self._update_predictive_diagnostics(kind, override="⚠️ Mode inconnu")
            return
        self._recover("chauffage_mode")

        pool_mode = self._mode()
        if pool_mode in [TAB_MODE[2], TAB_MODE[4]] or self.arret_force_actif():
            self.chauffage_predictif_heat_requested = False
            self._cancel_chauffage_start()
            self._update_predictive_diagnostics(kind, override="⛔ Sécurité piscine")
            self._pac_off("hors gel / arrêt forcé", post=(pool_mode != TAB_MODE[4]))
            return

        if kind == "disabled":
            self.chauffage_predictif_heat_requested = False
            self._cancel_chauffage_start()
            self._cancel_pac_start()
            self._update_predictive_diagnostics(kind, override="⏸ Chauffage désactivé")
            self._pac_off("chauffage désactivé")
            return

        if kind == "season_start":
            self._cancel_pac_start()
            return self._manage_debut_saison()

        if kind == "turbo":
            self._cancel_pac_start()
            self._update_predictive_diagnostics(kind, override="🔥 Turbo")
            return self._request_chauffage_start(self.chauffage_preset_turbo, "Turbo")

        if self.chauffage_predictif and kind in {"auto", "end_season"}:
            if kind == "auto":
                if not self.gestion_pac_auto:
                    self._update_predictive_diagnostics(
                        kind, override="⏸ Auto PAC non piloté"
                    )
                    return
                if hasattr(self, "mode_auto_autorise") and not self.mode_auto_autorise():
                    self.chauffage_predictif_heat_requested = False
                    self._cancel_chauffage_start()
                    self._update_predictive_diagnostics(
                        kind, override="⏸ Automatique suspendu"
                    )
                    return
            self._cancel_pac_start()
            return self._manage_predictive_heating(kind)

        if kind == "auto":
            self._cancel_chauffage_start()
            result = super()._manage_pac_auto()
            if self._pac_state() == "heat":
                self._set_pac_preset(self.chauffage_preset_auto)
            return result

        # Legacy end-of-season behavior when the predictive engine is disabled.
        if kind == "end_season":
            self._cancel_pac_start()
            return self._request_chauffage_start(
                self.chauffage_preset_smart,
                "fin de saison",
            )

    def pac_besoin_chauffe(self):
        if self.entity_chauffage:
            kind = chauffage_mode_kind(self.chauffage_mode())
            if kind in {"season_start", "turbo"}:
                return True
            if kind == "end_season" and not self.chauffage_predictif:
                return True
            if kind in {"auto", "end_season"} and self.chauffage_predictif:
                return bool(self.chauffage_predictif_heat_requested)
        return super().pac_besoin_chauffe()
''',
    "replace heating policy core",
)

PATH.write_text(text, encoding="utf-8")
print("pool_heating.py patched")
