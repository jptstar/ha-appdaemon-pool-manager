# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar


class AutoModeGateMixin:
    """Gate automatic PAC policy behind an optional Home Assistant boolean.

    When ``entity_mode_auto`` is configured, automatic PAC start/stop decisions
    are suspended unless that entity is ``on``. Manual/override behavior and
    the PAC flow fail-safe remain independent of this gate.

    If no entity is configured, behavior stays backward-compatible.
    """

    def initialize(self):
        self.entity_mode_auto = self.args.get("entity_mode_auto")
        super().initialize()

        if self.entity_mode_auto:
            self.listen_state(
                self.change_mode_auto,
                self.entity_mode_auto,
            )

    def mode_auto_autorise(self):
        """Return whether automatic pool/PAC policy is currently authorized."""
        if not self.entity_mode_auto:
            return True
        try:
            return self.get_state(self.entity_mode_auto) == "on"
        except Exception:
            return False

    def change_mode_auto(self, entity, attribute, old, new, kwargs):
        """Apply the automatic-policy gate immediately when HA changes it."""
        if not self.mode_auto_autorise():
            self._cancel_pac_start()
        self.safety_tick({})

    def _manage_pac_auto(self):
        """Suspend normal automatic PAC policy while pool auto mode is off."""
        if getattr(self, "gestion_pac_auto", False) and not self.mode_auto_autorise():
            self._cancel_pac_start()
            return
        return super()._manage_pac_auto()

    def _check_pac_start(self, kwargs):
        """Abort a pending PAC start if auto mode is disabled mid-sequence."""
        if getattr(self, "gestion_pac_auto", False) and not self.mode_auto_autorise():
            # The callback that was scheduled is now executing, so clear its
            # handle before cancelling the logical pending-start state.
            self.handle_pac_auto_start = None
            self._cancel_pac_start()
            return
        return super()._check_pac_start(kwargs)
