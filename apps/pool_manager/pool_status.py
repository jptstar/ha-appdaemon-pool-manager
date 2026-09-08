# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

from pool_common import build_quota_status


class StatusMixin:
    """Optional structured quota/status output for Home Assistant."""

    def set_messages(self, short_msg, detail_msg=""):
        # Preserve the existing status/detail outputs exactly as before.
        super().set_messages(short_msg, detail_msg)
        self.set_quota_status(short_msg)

    def set_quota_status(self, decision):
        entity_id = self.args.get("message_filtration_quota")
        if not entity_id:
            return

        objectif_entity = self.args.get("duree_filtration_ete")
        objectif = self.get_float_state(objectif_entity, 0.0) if objectif_entity else 0.0
        effectue = max(0.0, float(getattr(self, "temps_filtration_equivalent_jour", 0.0) or 0.0))

        # Existing short messages are generally "Decision | progress".
        # Only the decision label is needed in the structured summary.
        decision_txt = str(decision).split("|", 1)[0].strip()
        texte = build_quota_status(objectif, effectue, decision_txt)

        if getattr(self, "last_quota_status", None) == texte:
            return

        self.last_quota_status = texte
        try:
            self.call_service("input_text/set_value", entity_id=entity_id, value=texte)
        except Exception as e:
            self.log(f"⚠️ Erreur statut quota : {e}", log="piscine_log")
