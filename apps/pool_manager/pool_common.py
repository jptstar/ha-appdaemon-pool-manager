# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import datetime
from datetime import timedelta

TAB_MODE = [
    "Température",
    "Intelligent",
    "Hors Gel",
    "Marche Forcée",
    "Arrêt Forcé"
]

JOURNAL = 2


def duree_abaque(temperature_eau):
    t = max(float(temperature_eau), 10.0)
    return 0.00335 * t**3 - 0.14953 * t**2 + 2.43489 * t - 10.72859


def duree_classique(temperature_eau):
    return max(float(temperature_eau), 10.0) / 2.0


def calcule_objectif_filtration(temperature_eau, coef, mode_abaque):
    """Calculate the daily equivalent filtration target, capped to one day."""
    base_time = duree_abaque(temperature_eau) if mode_abaque else duree_classique(temperature_eau)
    return min(max(0.0, base_time * float(coef)), 24.0)


def format_duree_hm(hours):
    """Format decimal hours as a stable human-readable hours/minutes value."""
    try:
        total_minutes = int(round(max(0.0, float(hours)) * 60.0))
    except (TypeError, ValueError):
        total_minutes = 0

    heures, minutes = divmod(total_minutes, 60)
    return f"{heures} h {minutes:02d}"


def build_quota_status(objectif, effectue, decision):
    """Build the optional Home Assistant quota summary line."""
    try:
        objectif_h = min(max(0.0, float(objectif)), 24.0)
    except (TypeError, ValueError):
        objectif_h = 0.0

    try:
        effectue_h = max(0.0, float(effectue))
    except (TypeError, ValueError):
        effectue_h = 0.0

    restant_h = max(0.0, objectif_h - effectue_h)
    decision_txt = str(decision).strip() or "—"

    return (
        f"BESOIN {format_duree_hm(objectif_h)} | "
        f"EFFECTUÉ {format_duree_hm(effectue_h)} | "
        f"RESTANT {format_duree_hm(restant_h)} | "
        f"DÉCISION {decision_txt}"
    )


def en_heure(t):
    h = int(t)
    m = int((t - h) * 60)
    s = int((((t - h) * 60) - m) * 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def heure_to_timedelta(hhmmss):
    return timedelta(
        hours=int(hhmmss[0:2]),
        minutes=int(hhmmss[3:5]),
        seconds=int(hhmmss[6:8])
    )


def calcule_plage_filtration(temps_filtration, pivot_txt):
    """Return a valid daily filtration window without crossing day boundaries."""
    duree_h = max(0.0, min(float(temps_filtration), 24.0))
    jour_max = timedelta(hours=23, minutes=59, seconds=59)

    if duree_h >= 24.0:
        return timedelta(0), jour_max

    duree = timedelta(hours=duree_h)
    h_pivot = heure_to_timedelta(pivot_txt)
    demi = duree / 2

    h_debut = h_pivot - demi
    h_fin = h_pivot + demi

    # Shift the whole window when it would cross midnight so the requested
    # duration is preserved as much as possible inside the current day.
    if h_debut < timedelta(0):
        h_fin += -h_debut
        h_debut = timedelta(0)

    if h_fin > jour_max:
        depassement = h_fin - jour_max
        h_debut = max(timedelta(0), h_debut - depassement)
        h_fin = jour_max

    return h_debut, h_fin
