# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import datetime
from datetime import timedelta

from pool_common import TAB_MODE, heure_to_timedelta


def moyenne_reference_temperature(previous, measured, alpha=0.35):
    """Return a smoothed representative pool-water temperature.

    The persisted value is deliberately a thermal reference, not an attempt to
    reproduce every short sensor fluctuation. If the previous value is missing
    or implausible, the new physically measured value becomes the reference.
    """
    try:
        mesure = float(measured)
    except (TypeError, ValueError):
        return None

    try:
        precedent = float(previous)
    except (TypeError, ValueError):
        return mesure

    if not (2.0 <= precedent <= 45.0):
        return mesure

    a = max(0.0, min(1.0, float(alpha)))
    return precedent + a * (mesure - precedent)


def progression_solaire(now, start, end, objectif_temps_eq):
    """Return the expected equivalent filtration progress over daylight."""
    objectif = max(0.0, float(objectif_temps_eq))
    if objectif <= 0.0 or end <= start:
        return 0.0
    if now <= start:
        return 0.0
    if now >= end:
        return round(objectif, 3)
    ratio = (now - start).total_seconds() / (end - start).total_seconds()
    return round(objectif * max(0.0, min(1.0, ratio)), 3)


def brassage_nuit_intelligent_autorise(mode, enabled):
    """Night mixing is optional in Intelligent mode; freeze mixing is separate."""
    return mode != TAB_MODE[1] or bool(enabled)


class DaylightMixin:
    """Daylight-aware Intelligent mode and persisted thermal-reference handling."""

    def initialize(self):
        b = lambda key, default: str(self.args.get(key, default)).lower() == "true"

        # Intelligent mode follows actual daylight when AppDaemon's sun API is
        # available. Fixed heures remain a transparent fallback.
        self.suivre_soleil_reel = b("suivre_soleil_reel", "true")
        self.marge_apres_lever_soleil_min = int(
            float(self.args.get("marge_apres_lever_soleil_min", 0))
        )
        self.marge_avant_coucher_soleil_min = int(
            float(self.args.get("marge_avant_coucher_soleil_min", 0))
        )

        # Night mixing in Intelligent mode used mainly to refresh temperature.
        # It is no longer needed once the persisted thermal reference is used.
        # Hors Gel circulation is implemented elsewhere and is never disabled by
        # this setting.
        self.brassage_nuit_intelligent = b("brassage_nuit_intelligent", "false")

        # mem_temp becomes a slowly moving thermal reference. Once circulation
        # has run long enough, ControlMixin still uses the real water probe for
        # the live target; only the value persisted for the next stop/night is
        # smoothed here.
        self.temperature_reference_lissee = b("temperature_reference_lissee", "true")
        self.temperature_reference_alpha = max(
            0.0,
            min(1.0, float(self.args.get("temperature_reference_alpha", 0.35))),
        )
        self.temperature_reference_min_update_s = max(
            60,
            int(float(self.args.get("temperature_reference_min_update_s", 1800))),
        )
        self.last_temperature_reference_update = None

        super().initialize()

    # ---------------------------- daylight window ----------------------------
    @staticmethod
    def _now_like(reference):
        if reference is not None and getattr(reference, "tzinfo", None) is not None:
            return datetime.datetime.now(reference.tzinfo)
        return datetime.datetime.now()

    @staticmethod
    def _normalise_awareness(a, b):
        if a.tzinfo is None and b.tzinfo is not None:
            return a.replace(tzinfo=b.tzinfo), b
        if a.tzinfo is not None and b.tzinfo is None:
            return a, b.replace(tzinfo=a.tzinfo)
        return a, b

    def _daylight_bounds(self):
        """Return today's effective sunrise/sunset bounds, or None on fallback.

        AppDaemon exposes the *next* sunrise and sunset. When the sun is already
        up, today's sunrise is therefore approximated by next sunrise minus one
        day. The approximation is only used to pace the daily quota; actual
        daylight gating also checks the sun state itself.
        """
        if not self.suivre_soleil_reel:
            return None
        try:
            next_rise = self.sunrise()
            next_set = self.sunset()
            if not isinstance(next_rise, datetime.datetime) or not isinstance(next_set, datetime.datetime):
                return None
            next_rise, next_set = self._normalise_awareness(next_rise, next_set)
            now = self._now_like(next_rise)
            sun_is_up = bool(self.sun_up())

            if sun_is_up:
                start = next_rise - timedelta(days=1)
                end = next_set
            elif next_set.date() == now.date():
                # Before today's sunrise.
                start = next_rise
                end = next_set
            else:
                # After today's sunset: both next events belong to tomorrow.
                start = next_rise - timedelta(days=1)
                end = next_set - timedelta(days=1)

            start += timedelta(minutes=self.marge_apres_lever_soleil_min)
            end -= timedelta(minutes=self.marge_avant_coucher_soleil_min)
            if end <= start:
                return None
            return start, end
        except Exception:
            return None

    def _fixed_range_active(self, debut, fin):
        now_td = self.td_now()
        start = heure_to_timedelta(debut)
        end = heure_to_timedelta(fin)
        if start <= end:
            return start <= now_td <= end
        return now_td >= start or now_td <= end

    def _daylight_active(self):
        bounds = self._daylight_bounds()
        if bounds is None:
            return self._fixed_range_active(self.heure_debut_solaire, self.heure_fin_solaire)
        start, end = bounds
        now = self._now_like(start)
        try:
            # Use the actual state as the hard gate so the next-event
            # reconstruction cannot start filtration before sunrise.
            return bool(self.sun_up()) and start <= now <= end
        except Exception:
            return start <= now <= end

    def est_dans_plage(self, debut, fin):
        # Replace only the main Intelligent solar window. Other configured time
        # windows retain their previous semantics.
        if (
            self.suivre_soleil_reel
            and debut == self.heure_debut_solaire
            and fin == self.heure_fin_solaire
        ):
            return self._daylight_active()

        # Evening catch-up begins at the real sunset when daylight tracking is
        # active, then keeps the existing configured end (22:00 at JP's site).
        if (
            self.suivre_soleil_reel
            and debut == self.heure_fin_solaire
            and fin == self.heure_fin_rattrapage
        ):
            bounds = self._daylight_bounds()
            if bounds is not None:
                _, daylight_end = bounds
                now = self._now_like(daylight_end)
                if now < daylight_end:
                    return False
                end_td = heure_to_timedelta(fin)
                now_td = timedelta(hours=now.hour, minutes=now.minute, seconds=now.second)
                return now_td <= end_td

        return self._fixed_range_active(debut, fin)

    def progression_attendue(self, objectif_temps_eq):
        bounds = self._daylight_bounds()
        if bounds is None:
            return super().progression_attendue(objectif_temps_eq)
        start, end = bounds
        now = self._now_like(start)
        return progression_solaire(now, start, end, objectif_temps_eq)

    def temps_restant_plage_solaire_h(self):
        bounds = self._daylight_bounds()
        if bounds is None:
            return super().temps_restant_plage_solaire_h()
        _, end = bounds
        now = self._now_like(end)
        if now >= end:
            return 0.0
        return max(0.0, (end - now).total_seconds() / 3600.0)

    def stabilite_surplus_ok(self, surplus_disponible):
        # This also closes the old loophole where a large electrical export could
        # start the pump before the configured solar period.
        if self.suivre_soleil_reel and not self._daylight_active():
            self.debut_stabilite_surplus = None
            return False, self.tempo_stabilite_surplus
        return super().stabilite_surplus_ok(surplus_disponible)

    # -------------------------- Intelligent night mixing --------------------------
    def is_night_brassage_slot(self, heure_actuelle):
        try:
            mode = (self.get_state(self.args["mode_de_fonctionnement"]) or "").strip()
        except Exception:
            mode = None
        if not brassage_nuit_intelligent_autorise(mode, self.brassage_nuit_intelligent):
            return False
        return super().is_night_brassage_slot(heure_actuelle)

    # -------------------------- thermal reference memory --------------------------
    @staticmethod
    def _valid_float(value):
        if value is None or str(value).strip().lower() in {"unknown", "unavailable", "none", ""}:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _physical_water_temperature(self):
        entity = self.args.get("temperature_eau")
        if not entity:
            return None
        try:
            return self._valid_float(self.get_state(entity))
        except Exception:
            return None

    def set_value(self, entity_id, value):
        mem_entity = self.args.get("mem_temp")
        if (
            self.temperature_reference_lissee
            and mem_entity
            and entity_id == mem_entity
            and getattr(self, "fin_tempo", 0) == 1
        ):
            # Never contaminate the persisted thermal reference with a fail-safe
            # substituted temperature. Only a valid physical probe may update it.
            measured = self._physical_water_temperature()
            if measured is None:
                return None

            now = datetime.datetime.now()
            if self.last_temperature_reference_update is not None:
                elapsed = (now - self.last_temperature_reference_update).total_seconds()
                if elapsed < self.temperature_reference_min_update_s:
                    return None

            try:
                previous = self.get_state(mem_entity)
            except Exception:
                previous = None
            reference = moyenne_reference_temperature(
                previous,
                measured,
                self.temperature_reference_alpha,
            )
            if reference is None:
                return None

            self.last_temperature_reference_update = now
            return super().set_value(entity_id, round(reference, 2))

        return super().set_value(entity_id, value)
