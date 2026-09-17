# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Cross-cutting runtime guards for pool control stability."""

import datetime

from pool_common import TAB_MODE


class RuntimeStabilityMixin:
    """Prevent control chatter across predictive heating and pump handoff."""

    @staticmethod
    def _bool_arg(value, default=False):
        if value is None:
            return bool(default)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _clock(value, default):
        try:
            return datetime.time.fromisoformat(str(value or default))
        except (TypeError, ValueError):
            return datetime.time.fromisoformat(default)

    @staticmethod
    def _time_in_window(value, start, end):
        if start <= end:
            return start <= value < end
        return value >= start or value < end

    def initialize(self):
        # Intelligent mode can run without periodic night mixing because the
        # persisted water reference is used while circulation is stopped.
        self.brassage_nuit_intelligent = self._bool_arg(
            self.args.get("brassage_nuit_intelligent", "false")
        )

        # Aquagem/iSaver local control regains authority roughly one minute after
        # the last remote write. When Pool Manager intentionally disables local
        # handoff, refresh the last desired speed before that watchdog expires.
        self.pompe_remote_keepalive_s = max(
            15,
            int(float(self.args.get("pompe_remote_keepalive_s", 30))),
        )

        # A predictive heating decision must not flip every 30 seconds around a
        # moving schedule boundary. Once a valid slot starts, keep it active for
        # its planned duration. Unscheduled heat requests get a short minimum-on
        # latch to avoid rapid Heat/Off cycling.
        self.chauffage_predictif_tempo_min_on_s = max(
            60,
            int(float(self.args.get("chauffage_predictif_tempo_min_on_s", 900))),
        )
        self._predictive_hold_until = None
        self._predictive_hold_target_c = None
        self._predictive_hold_kind = None

        super().initialize()

    # ---------------------------- night circulation ----------------------------

    def is_night_brassage_slot(self, heure_actuelle):
        """Honor the Intelligent-mode night-mixing opt-out.

        Older code documented ``brassage_nuit_intelligent: false`` but the slot
        detector ignored it, so Intelligent mode still started 20 minutes of
        circulation at the beginning of every hour.
        """
        try:
            mode = (self.get_state(self.args["mode_de_fonctionnement"]) or "").strip()
        except Exception:
            mode = None

        if mode == TAB_MODE[1] and not self.brassage_nuit_intelligent:
            return False
        return super().is_night_brassage_slot(heure_actuelle)

    # ---------------------------- iSaver ownership -----------------------------

    def _maintain_remote_pump_authority(self, now=None):
        """Re-assert the last desired speed while local-panel handoff is disabled."""
        if not getattr(self, "entity_pompe_local_panel_assist", None):
            return False
        if self.arret_force_actif() or not self.pompe_est_on():
            return False
        if self.start_sequence_is_running() or self.stop_sequence_is_running():
            return False

        try:
            mode = (self.get_state(self.args["mode_de_fonctionnement"]) or "").strip()
        except Exception:
            return False

        # Température and Marche Forcée deliberately allow local-panel takeover.
        # Intelligent and Hors Gel are the modes where Pool Manager must remain
        # the single speed authority.
        if mode not in [TAB_MODE[1], TAB_MODE[2]]:
            return False

        try:
            if self.get_state(self.entity_pompe_local_panel_assist) == "on":
                return False
        except Exception:
            return False

        target = getattr(self, "derniere_vitesse_commande", None)
        if target is None:
            return False

        now = now or datetime.datetime.now()
        last_change = getattr(self, "last_changement_vitesse", None)
        if last_change is not None:
            elapsed = (now - last_change).total_seconds()
            if elapsed < self.pompe_remote_keepalive_s:
                return False

        self.set_pump_percentage(target, force=True)
        return True

    def check_etats_speciaux(self, kwargs):
        """Run normal housekeeping, then keep remote pump ownership alive."""
        super().check_etats_speciaux(kwargs)
        self._maintain_remote_pump_authority()

    # ------------------------- predictive anti-chatter -------------------------

    def _clear_predictive_hold(self):
        self._predictive_hold_until = None
        self._predictive_hold_target_c = None
        self._predictive_hold_kind = None

    def _predictive_emergency_allowed(self, plan, now):
        """Allow emergency heating only inside intentional heating periods.

        The pure scheduler can otherwise fill missing capacity by inserting an
        emergency segment starting *now*. At 05:xx this bypassed the configured
        07:00 morning start and made the pump/PAC wake before the intended window.
        """
        active = (plan or {}).get("active_segment") or {}
        if active.get("kind") != "emergency":
            return True

        candidate = (plan or {}).get("candidate") or {}
        target_day = candidate.get("date")
        if target_day is None:
            return False

        current_time = now.time()
        if now.date() < target_day:
            start = self._clock(
                getattr(self, "chauffage_predictif_veille_debut", None),
                "12:00:00",
            )
            end = self._clock(
                getattr(self, "chauffage_predictif_veille_fin", None),
                "20:00:00",
            )
            return self._time_in_window(current_time, start, end)

        if now.date() == target_day:
            morning_start = self._clock(
                getattr(self, "chauffage_predictif_matin_debut", None),
                "07:00:00",
            )
            swim_datetime = candidate.get("swim_datetime")
            if isinstance(swim_datetime, datetime.datetime):
                return morning_start <= current_time < swim_datetime.time()
            return current_time >= morning_start

        return False

    def _suppress_out_of_window_predictive_emergency(self, plan, now):
        if not plan or self._predictive_emergency_allowed(plan, now):
            return plan

        active = plan.get("active_segment")
        schedule = [
            item
            for item in (plan.get("schedule") or [])
            if item is not active and item != active
        ]

        next_segment = None
        for item in schedule:
            start = item.get("start")
            if isinstance(start, datetime.datetime) and start > now:
                next_segment = item
                break

        plan["schedule"] = schedule
        plan["scheduled_hours"] = round(
            sum(float(item.get("hours") or 0.0) for item in schedule),
            2,
        )
        plan["active_segment"] = None
        plan["next_segment"] = next_segment
        plan["should_heat"] = False
        plan["heat_target_c"] = None

        if next_segment is not None:
            plan["reason"] = (
                "attente plage de chauffe autorisée; prochain créneau "
                f"{next_segment['start'].strftime('%d/%m %H:%M')}"
            )
        else:
            plan["reason"] = "attente plage de chauffe autorisée"
        return plan

    def _stabilize_predictive_plan(self, plan, kind, water, target, now=None):
        """Latch an active predictive slot so tiny recalculations cannot chatter."""
        now = now or datetime.datetime.now()
        plan = self._suppress_out_of_window_predictive_emergency(plan, now)
        margin = max(
            0.0,
            float(getattr(self, "chauffage_predictif_marge_arret_c", 0.2)),
        )

        if self._predictive_hold_kind not in [None, kind]:
            self._clear_predictive_hold()

        hold_until = self._predictive_hold_until
        hold_target = self._predictive_hold_target_c

        if (
            hold_until is not None
            and hold_target is not None
            and water is not None
            and float(water) >= float(hold_target) - margin
        ):
            self._clear_predictive_hold()
            hold_until = None
            hold_target = None

        if plan.get("should_heat"):
            desired_target = plan.get("heat_target_c")
            if desired_target is None:
                desired_target = target

            active = plan.get("active_segment") or {}
            segment_end = active.get("end")
            if isinstance(segment_end, datetime.datetime) and segment_end > now:
                # Never extend a scheduled slot past its configured end.
                desired_until = segment_end
            else:
                desired_until = now + datetime.timedelta(
                    seconds=self.chauffage_predictif_tempo_min_on_s
                )

            if hold_until is None or desired_until > hold_until:
                hold_until = desired_until

            self._predictive_hold_until = hold_until
            self._predictive_hold_target_c = desired_target
            self._predictive_hold_kind = kind
            return plan

        if hold_until is not None and now < hold_until:
            plan["should_heat"] = True
            plan["heat_target_c"] = hold_target if hold_target is not None else target
            plan["reason"] = (
                "créneau prédictif stabilisé jusqu’à "
                f"{hold_until.strftime('%H:%M')}"
            )
            return plan

        self._clear_predictive_hold()
        return plan

    def _build_runtime_predictive_plan(self, kind, water, target, forecast):
        plan, rate = super()._build_runtime_predictive_plan(
            kind,
            water,
            target,
            forecast,
        )
        return (
            self._stabilize_predictive_plan(plan, kind, water, target),
            rate,
        )

    def change_chauffage_mode(self, entity, attribute, old, new, kwargs):
        self._clear_predictive_hold()
        return super().change_chauffage_mode(entity, attribute, old, new, kwargs)
