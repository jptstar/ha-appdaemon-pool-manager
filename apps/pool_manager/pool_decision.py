# SPDX-License-Identifier: GPL-3.0-only
"""Bounded, expiring user decisions; all heating still uses normal safety gates."""

import datetime
import uuid


class DecisionSupport:
    def _initialize_pool_decisions(self):
        self._pool_pending = None
        self._pool_decision_day = None
        self._pool_decision_choice = None
        self._pool_notice_key = None
        self._pool_notices = set()
        # Keep an explicit daily choice through AppDaemon/HACS reloads. Old
        # notification tokens are deliberately not restored.
        try:
            previous = self.get_state(
                self.args.get(
                    "entity_pool_decision", "sensor.piscine_decision_chauffage"
                ),
                attribute="all",
            )
            attrs = previous.get("attributes", {}) if isinstance(previous, dict) else {}
            today = datetime.date.today()
            if attrs.get("choice_day") == today.isoformat():
                self._pool_decision_day = today
                self._pool_decision_choice = attrs.get("choice")
            if attrs.get("notice_day") == today.isoformat():
                self._pool_notices = {
                    (today, r) for r in attrs.get("notice_reasons", [])
                }
        except Exception:
            pass
        self.listen_event(
            self._pool_notification_action, "mobile_app_notification_action"
        )

    def _publish_pool_decision(self):
        pending = getattr(self, "_pool_pending", None)
        if pending and datetime.datetime.now() >= pending["expires"]:
            self._pool_pending = pending = None
        attributes = {
            "friendly_name": "Piscine • Confirmation chauffage",
            "icon": "mdi:message-question",
            "choice": getattr(self, "_pool_decision_choice", None),
            "choice_day": self._pool_decision_day.isoformat()
            if self._pool_decision_day
            else None,
            "notice_day": datetime.date.today().isoformat(),
            "notice_reasons": [
                r for d, r in self._pool_notices if d == datetime.date.today()
            ],
        }
        if pending:
            attributes.update(
                expires=pending["expires"].isoformat(),
                message=pending["message"],
                **{c: a for a, c in pending["actions"].items()},
            )
        try:
            self.set_state(
                self.args.get("entity_pool_decision", "sensor.piscine_decision_chauffage"),
                state="À confirmer" if pending else "Aucune demande",
                attributes=attributes,
                replace=True,
            )
        except Exception as exc:
            self.log(f"Diagnostic confirmation indisponible : {exc}", log="piscine_log")

    def _pool_notify(self, message, actions=None):
        service = str(self.args.get("pool_notify_service") or "").replace(".", "/", 1)
        try:
            if service.startswith("notify/"):
                self.call_service(
                    service,
                    title="Piscine • Décision chauffage",
                    message=message,
                    data={"tag": "pool_manager_decision", "actions": actions or []},
                )
            else:
                self.call_service(
                    "persistent_notification/create",
                    title="Piscine • Décision chauffage",
                    message=message,
                    notification_id="pool_manager_decision",
                )
        except Exception as exc:
            self.log(f"Notification chauffage indisponible : {exc}", log="piscine_log")

    def _pool_notification_action(self, event_name, data, kwargs):
        pending = getattr(self, "_pool_pending", None)
        now = datetime.datetime.now()
        if not pending or now >= pending["expires"]:
            return
        choice = pending["actions"].get(data.get("action"))
        if choice is None:
            return
        # Invalidate before applying: duplicated mobile events cannot extend Turbo.
        self._pool_pending = None
        entity = getattr(self, "entity_chauffage", None)
        if not entity or self.get_state(entity) != pending["mode"]:
            return
        if self.arret_force_actif():
            return
        if choice == "turbo":
            try:
                self.call_service(
                    "input_select/select_option", entity_id=entity, option="Turbo • 1 h"
                )
            except Exception:
                self._publish_pool_decision()
                self._pool_notify(
                    "La demande Turbo a échoué. Vérifier le sélecteur Chauffage piscine."
                )
                return
        self._pool_decision_day = now.date()
        self._pool_decision_choice = choice
        self._publish_pool_decision()
        self._pool_notify(
            {
                "eco": "Économie retenue pour aujourd'hui : Smart de jour.",
                "turbo": "Turbo 1 h demandé ; les protections habituelles restent actives.",
                "skip": "Chauffage prédictif suspendu jusqu'à minuit.",
            }[choice]
        )
        self.traitement({})

    def _apply_pool_decision(self, plan, water, target, now=None, economy_plan=None):
        now = now or datetime.datetime.now()
        if str(self.args.get("pool_confirmation_enabled", "true")).lower() != "true":
            return plan
        plan = dict(plan)
        missed = plan.get("missed_swim_dates") or []
        upcoming_night = any(
            now.date() <= row["date"] <= now.date() + datetime.timedelta(days=1)
            and float(row.get("night_heat_hours") or 0) > 0
            for row in plan.get("mpc_plan") or []
            if isinstance(row.get("date"), datetime.date)
        )
        exceptional = bool(
            missed
            or upcoming_night
            or plan.get("night_heating")
            or (
                plan.get("should_heat")
                and plan.get("preset")
                == getattr(self, "chauffage_preset_turbo", "Turbo")
            )
        )
        choice = (
            getattr(self, "_pool_decision_choice", None)
            if getattr(self, "_pool_decision_day", None) == now.date()
            else None
        )
        if not exceptional and choice != "skip":
            self._pool_pending = None
            if hasattr(self, "_pool_notices"):
                self._publish_pool_decision()
            return plan
        reason = (
            "Objectif baignade inaccessible selon le modèle"
            if missed
            else "Chauffe nocturne proposée"
            if upcoming_night or plan.get("night_heating")
            else "Turbo proposé"
        )
        key = (now.date(), reason)
        notices = {
            k for k in getattr(self, "_pool_notices", set()) if k[0] == now.date()
        }
        self._pool_notices = notices
        if choice is None and key not in notices:
            token = uuid.uuid4().hex
            actions = {
                f"POOL_{token}_{name}": name for name in ("eco", "turbo", "skip")
            }
            self._pool_pending = {
                "expires": min(
                    now + datetime.timedelta(minutes=30),
                    datetime.datetime.combine(
                        now.date() + datetime.timedelta(days=1), datetime.time()
                    ),
                ),
                "actions": actions,
                "mode": self.get_state(self.entity_chauffage),
            }
            self._pool_notice_key = key
            notices.add(key)
            labels = {
                "eco": "Économie",
                "turbo": "Turbo 1 h",
                "skip": "Suspendre aujourd'hui",
            }
            message = (
                f"{reason}. Eau estimée {water:.1f} °C ; cible {target:.1f} °C. "
                "Turbo 1 h ne garantit pas la cible. Sans réponse : Smart de jour. "
                "Choix valable 30 minutes. Sans boutons, utiliser le sélecteur Chauffage piscine."
            )
            self._pool_pending["message"] = message
            self._pool_notify(
                message,
                [
                    {"action": a, "title": labels[c], "authenticationRequired": True}
                    for a, c in actions.items()
                ],
            )
        if choice == "skip":
            plan.update(
                should_heat=False,
                heat_target_c=None,
                action="WAIT",
                reason="suspension demandée jusqu'à minuit",
            )
        elif exceptional:
            if economy_plan is not None:
                plan = dict(economy_plan())
                plan["missed_swim_dates"] = sorted(
                    set(missed + (plan.get("missed_swim_dates") or []))
                )
            # Default is economy, including after timeout/restart. Explicit
            # Turbo is handled by the existing one-hour selector/timer.
            plan["preset"] = self.chauffage_preset_smart
            if not self._predictive_daylight_active():
                plan.update(should_heat=False, heat_target_c=None, action="WAIT")
            plan["reason"] = f"{reason}; économie en attendant confirmation"
        plan["decision_required"] = exceptional and choice is None
        plan["decision_choice"] = choice or "eco"
        self._publish_pool_decision()
        return plan
