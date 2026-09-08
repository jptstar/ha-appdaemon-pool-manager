# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

from pool_common import build_quota_status, remove_legacy_progress_fragments


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
        detail_enrichi = f"{quota_txt} | {detail_clean}" if detail_clean else quota_txt

        # Reuse the two existing Home Assistant input_text helpers. No third
        # quota helper is required.
        super().set_messages(decision_txt, detail_enrichi)
