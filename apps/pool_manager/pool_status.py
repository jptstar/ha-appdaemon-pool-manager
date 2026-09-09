# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import re

from pool_common import build_quota_status, remove_legacy_progress_fragments


_TIMER_DETAIL_PATTERNS = (
    (re.compile(r"^attente on \d+s$", re.IGNORECASE), "temporisation démarrage"),
    (re.compile(r"^stabilité \d+s$", re.IGNORECASE), "attente surplus stable"),
    (re.compile(r"^arrêt dans \d+s$", re.IGNORECASE), "temporisation arrêt"),
    (re.compile(r"^redémarrage dans \d+s$", re.IGNORECASE), "temporisation redémarrage"),
    (re.compile(r"^anti-coupure \d+s$", re.IGNORECASE), "anti-coupure"),
    (re.compile(r"^maintien \d+s$", re.IGNORECASE), "maintien"),
)

_DYNAMIC_ELECTRICAL_DETAIL_RE = re.compile(
    r"^(?:surplus|réseau|pv|pompe)\s+[+-]?\d+(?:[.,]\d+)?\s*w(?:\s+(?:réel|estimé))?$",
    re.IGNORECASE,
)


def compact_detail_status(detail):
    """Stabilize the HA detail text so timers do not create state-history spam.

    Countdown values are converted to semantic states and instantaneous
    electrical values are omitted from this field because they are already
    exposed through the dedicated debug helper. Useful operational details
    such as speed, PAC state, quota decisions and deadlines are preserved.
    """
    compacted = []
    seen = set()

    for raw_part in str(detail or "").split("|"):
        part = raw_part.strip()
        if not part:
            continue

        if _DYNAMIC_ELECTRICAL_DETAIL_RE.match(part):
            continue

        for pattern, replacement in _TIMER_DETAIL_PATTERNS:
            if pattern.match(part):
                part = replacement
                break

        key = part.casefold()
        if key in seen:
            continue
        seen.add(key)
        compacted.append(part)

    return " | ".join(compacted)


class StatusMixin:
    """Keep Pool Manager status readable without requiring extra HA helpers."""

    def set_messages(self, short_msg, detail_msg=""):
        # Existing callers often append "| x.y/z.yh" to the short message.
        # The short field now stays focused on the current decision only.
        decision_txt = str(short_msg).split("|", 1)[0].strip()

        objectif_entity = self.args.get("duree_filtration_ete")
        objectif = self.get_float_state(objectif_entity, 0.0) if objectif_entity else 0.0
        effectue = max(0.0, float(getattr(self, "temps_filtration_equivalent_jour", 0.0) or 0.0))

        quota_txt = build_quota_status(objectif, effectue)
        detail_clean = remove_legacy_progress_fragments(detail_msg)
        detail_clean = compact_detail_status(detail_clean)
        detail_enrichi = f"{quota_txt} | {detail_clean}" if detail_clean else quota_txt

        # Reuse the two existing Home Assistant input_text helpers. No third
        # quota helper is required.
        super().set_messages(decision_txt, detail_enrichi)
