# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Home Assistant event-journal presentation, independent from control policy."""

import datetime


def journal_category(message):
    text = str(message or "").casefold()
    categories = (
        ("SÉCURITÉ", ("sécurité", "securite", "hors gel", "fail-safe", "⚠", "erreur", "fault")),
        ("APPRENTISSAGE", ("apprentissage", "pertes nuit", "°c/h")),
        ("ÉLECTROLYSE", ("électrolys", "electrolys")),
        ("VOLET", ("volet", "cover")),
        ("MESURE", ("température bassin certifiée", "température eau certifiée", "mesure température", "stabilisation", "calibration")),
        ("MPC", ("mpc", "prédictif", "predictif", "prévision", "forecast")),
        ("PAC", ("pac", "chauffage", "smart", "turbo")),
        ("POMPE", ("pompe", "vitesse", "circulation")),
        ("FILTRATION", ("filtration", "quota", "surplus", "rattrapage", "complément")),
    )
    for category, markers in categories:
        if any(marker in text for marker in markers):
            return category
    return "SYSTÈME"


def journal_french(message):
    text = str(message or "").strip()
    replacements = (
        ("startup_calibration", "mesure au démarrage"),
        ("morning_decision", "décision du matin"),
        ("heating_learning", "apprentissage chauffage"),
        ("target_check", "contrôle de consigne"),
        ("temperature_stabilization", "stabilisation température"),
        ("end_season", "fin de saison"),
        ("season_start", "début de saison"),
        ("PREHEAT", "préchauffage"),
        ("MAINTAIN", "maintien baignade"),
        ("PRESERVE", "préservation"),
        ("WAIT", "attente"),
        ("Heat/", "chauffage "),
        ("opening", "en ouverture"),
        ("closing", "en fermeture"),
        ("closed", "fermé"),
        ("open", "ouvert"),
        (" -> ", " → "),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    return text


def publish_journal_event(controller, message, now=None):
    entity = getattr(controller, "entity_pool_manager_log", None)
    if not entity:
        return False

    now = now or datetime.datetime.now()
    text = journal_french(message)
    if not text:
        return False
    category = journal_category(text)
    history = list(getattr(controller, "_pool_manager_log_history", []))
    if history:
        last = history[-1]
        if last.get("category") == category and last.get("message") == text:
            return False

    entry = {
        "timestamp": now.isoformat(timespec="seconds"),
        "category": category,
        "message": text[:500],
    }
    history.append(entry)
    history_limit = int(
        max(10, min(100, getattr(controller, "pool_manager_log_history_size", 50)))
    )
    history = history[-history_limit:]
    controller._pool_manager_log_history = history

    state = f"{now.strftime('%H:%M:%S')} • {category} • {text}"
    if len(state) > 180:
        state = state[:177] + "..."
    controller.set_state(
        entity,
        state=state,
        attributes={
            "friendly_name": "Piscine • Journal",
            "icon": "mdi:text-box-outline",
            "timestamp": entry["timestamp"],
            "category": category,
            "message": text,
            "history": history,
            "history_size": len(history),
            "history_limit": history_limit,
        },
        replace=True,
    )
    return True
