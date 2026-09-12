from pathlib import Path
import re


def replace_once(path: str, pattern: str, replacement: str, flags=0) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    new, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"Expected one replacement in {path}, got {count}")
    p.write_text(new, encoding="utf-8")


# ---------------------------------------------------------------------------
# pool_devices.py: revert the v0.4.5 last-command ownership experiment.
# Normal automatic control once again compares the requested speed with the
# physical percentage reported by Home Assistant. This keeps Intelligent mode
# authoritative and prevents stale targets from walking the pump unexpectedly.
# ---------------------------------------------------------------------------
devices = Path("apps/pool_manager/pool_devices.py")
text = devices.read_text(encoding="utf-8")
pattern = r"    def set_pump_percentage\(self, percentage, force=False\):\n.*?\n    def pompe_est_on\(self\):"
replacement = '''    def set_pump_percentage(self, percentage, force=False):
        percentage = int(max(0, min(100, percentage)))
        now = datetime.datetime.now()

        current = self.get_fan_percentage()
        if current is None:
            current = self.derniere_vitesse_commande if self.derniere_vitesse_commande is not None else percentage

        if not force and abs(percentage - current) < self.delta_vitesse_min:
            return current

        if not force and (now - self.last_changement_vitesse).total_seconds() < self.tempo_changement_vitesse:
            return current

        if not force:
            if percentage > current:
                percentage = min(current + self.pas_vitesse_max, percentage)
            elif percentage < current:
                percentage = max(current - self.pas_vitesse_max, percentage)

        try:
            self.call_service("fan/set_percentage", entity_id=self.args["fan_variateur_pompe"], percentage=percentage)
            self.derniere_vitesse_commande = percentage
            self.last_changement_vitesse = now
            self.maj_electrolyseur()
            return percentage
        except Exception as e:
            self.log(f"⚠️ Erreur set_percentage : {e}", log="piscine_log")
            return current

    def sync_local_panel_policy(self, mode=None):
        """Apply the optional pump-integration local-panel policy for this mode.

        Température and Marche Forcée deliberately allow the integration's local
        control assist. Intelligent and all safety-oriented modes keep remote
        control authoritative.
        """
        entity = getattr(self, "entity_pompe_local_panel_assist", None)
        if not entity:
            return

        if mode is None:
            mode = (self.get_state(self.args["mode_de_fonctionnement"]) or "").strip()

        allow_local = mode in [TAB_MODE[0], TAB_MODE[3]] and not self.arret_force_actif()
        desired = "on" if allow_local else "off"
        current = self.get_state(entity)
        if current == desired:
            return

        try:
            self.call_service(
                "switch/turn_on" if allow_local else "switch/turn_off",
                entity_id=entity,
            )
        except Exception as exc:
            self.log(f"⚠️ Erreur politique panneau local pompe : {exc}", log="piscine_log")

    def pompe_est_on(self):'''
new, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"Could not replace set_pump_percentage block: {count}")
text = new

# Reset one-shot manual-handoff initialization whenever the pump really stops.
old = '''    def turn_off_pompe_direct(self):
        if self.pompe_est_on():
            self.call_service("fan/turn_off", entity_id=self.args["cde_pompe"])
            self.last_pompe_off = datetime.datetime.now()
            self.derniere_vitesse_commande = None

        self.set_debug_w("")
'''
new = '''    def turn_off_pompe_direct(self):
        if self.pompe_est_on():
            self.call_service("fan/turn_off", entity_id=self.args["cde_pompe"])
            self.last_pompe_off = datetime.datetime.now()
            self.derniere_vitesse_commande = None

        self.mode_speed_initialized = False
        self.set_debug_w("")
'''
if old not in text:
    raise SystemExit("turn_off_pompe_direct block not found")
text = text.replace(old, new, 1)

# Mark the initial target as sent for the two modes where the user may later
# take over locally. Safety/automatic starts remain unchanged.
old = '''        try:
            self.set_pump_percentage(percentage, force=True)
            self.maj_electrolyseur()
        finally:
'''
new = '''        try:
            self.set_pump_percentage(percentage, force=True)
            if ctx.get("context") in {"temperature", "stabilisation_temperature", "marche_forcee"}:
                self.mode_speed_initialized = True
            self.maj_electrolyseur()
        finally:
'''
if old not in text:
    raise SystemExit("apply_pending_speed_after_start block not found")
text = text.replace(old, new, 1)
devices.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# lifecycle: optional Aquagem/local-panel entities and one-shot speed state.
# ---------------------------------------------------------------------------
lifecycle = Path("apps/pool_manager/pool_lifecycle.py")
text = lifecycle.read_text(encoding="utf-8")
anchor = '''        self.entity_pac_climate = self.args["entity_pac_climate"]
        self.entity_pac_conso = self.args["entity_pac_conso"]
        self.entity_pompe_conso = self.args["entity_pompe_conso"]
        self.entity_pv_power = self.args.get("entity_pv_power")
        self.entity_debug_w = self.args.get("entity_debug_w")
'''
insert = '''        self.entity_pac_climate = self.args["entity_pac_climate"]
        self.entity_pac_conso = self.args["entity_pac_conso"]
        self.entity_pompe_conso = self.args["entity_pompe_conso"]
        self.entity_pv_power = self.args.get("entity_pv_power")
        self.entity_debug_w = self.args.get("entity_debug_w")

        # Optional integration-level local-panel handoff (for example Aquagem).
        self.entity_pompe_local_panel_assist = self.args.get("entity_pompe_local_panel_assist")
        self.entity_pompe_local_control_available = self.args.get("entity_pompe_local_control_available")
        self.entity_pompe_local_control_remaining = self.args.get("entity_pompe_local_control_remaining")
        self.mode_speed_initialized = False
'''
if anchor not in text:
    raise SystemExit("lifecycle pump entity anchor not found")
text = text.replace(anchor, insert, 1)

anchor = '''        self.derniere_vitesse_commande = self.get_fan_percentage()

        self.listen_state(self.change_temp, self.args["temperature_eau"])
'''
insert = '''        self.derniere_vitesse_commande = self.get_fan_percentage()

        # On reload, do not disturb an already-running manual-capable mode.
        mode_init = (self.get_state(self.args["mode_de_fonctionnement"]) or "").strip()
        if self.pompe_est_on() and mode_init in [TAB_MODE[0], TAB_MODE[3]]:
            self.mode_speed_initialized = True
        self.sync_local_panel_policy(mode_init)

        self.listen_state(self.change_temp, self.args["temperature_eau"])
'''
if anchor not in text:
    raise SystemExit("lifecycle initialization anchor not found")
text = text.replace(anchor, insert, 1)

anchor = '''    def change_mode(self, entity, attribute, old, new, kwargs):
        self.fin_tempo = 0
        self.cancel_pending_start_sequence()
'''
insert = '''    def change_mode(self, entity, attribute, old, new, kwargs):
        self.fin_tempo = 0
        self.mode_speed_initialized = False
        self.cancel_pending_start_sequence()
'''
if anchor not in text:
    raise SystemExit("change_mode anchor not found")
text = text.replace(anchor, insert, 1)

anchor = '''        if new.strip() == TAB_MODE[2]:
            if not ("arret_force" in self.args and self.get_state(self.args["arret_force"]) == "on"):
                self.planifier_bras(0)

        self.traitement(kwargs)
'''
insert = '''        if new.strip() == TAB_MODE[2]:
            if not ("arret_force" in self.args and self.get_state(self.args["arret_force"]) == "on"):
                self.planifier_bras(0)

        self.sync_local_panel_policy(new.strip())
        self.traitement(kwargs)
'''
if anchor not in text:
    raise SystemExit("change_mode policy anchor not found")
text = text.replace(anchor, insert, 1)

# Forced-stop helper changes must also immediately revoke local-panel assist.
anchor = '''    def change_arret_force(self, entity, attribute, old, new, kwargs):
        if new == "on":
'''
insert = '''    def change_arret_force(self, entity, attribute, old, new, kwargs):
        self.sync_local_panel_policy()
        if new == "on":
'''
if anchor not in text:
    raise SystemExit("change_arret_force anchor not found")
text = text.replace(anchor, insert, 1)
lifecycle.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# control: Intelligent is authoritative; Température is one-shot then leaves
# local control alone; Marche Forcée already leaves running speed untouched.
# ---------------------------------------------------------------------------
control = Path("apps/pool_manager/pool_control.py")
text = control.read_text(encoding="utf-8")
anchor = '''        mode = self.get_state(self.args["mode_de_fonctionnement"]).strip()
        self.mode_actif = mode

        if self.arret_force_actif():
'''
insert = '''        mode = self.get_state(self.args["mode_de_fonctionnement"]).strip()
        self.mode_actif = mode
        self.sync_local_panel_policy(mode)

        if self.arret_force_actif():
'''
if anchor not in text:
    raise SystemExit("control mode anchor not found")
text = text.replace(anchor, insert, 1)

old = '''                        if not self.pompe_est_on():
                            self.start_pump_with_delayed_speed(self.vitesse_mode_temperature, delay_s=2, context="stabilisation_temperature")
                        else:
                            self.set_pump_percentage(self.vitesse_mode_temperature)

                        self.stabilisation_active = True
'''
new = '''                        if not self.pompe_est_on():
                            self.start_pump_with_delayed_speed(self.vitesse_mode_temperature, delay_s=2, context="stabilisation_temperature")
                        elif not self.mode_speed_initialized:
                            self.set_pump_percentage(self.vitesse_mode_temperature, force=True)
                            self.mode_speed_initialized = True

                        self.stabilisation_active = True
'''
if old not in text:
    raise SystemExit("temperature stabilization block not found")
text = text.replace(old, new, 1)

old = '''                vitesse_appliquee = self.set_pump_percentage(self.vitesse_mode_temperature)
                debit = self.debit_pompe(vitesse_appliquee)
'''
new = '''                if not self.mode_speed_initialized:
                    vitesse_appliquee = self.set_pump_percentage(self.vitesse_mode_temperature, force=True)
                    self.mode_speed_initialized = True
                else:
                    vitesse_appliquee = self.get_fan_percentage()
                    if vitesse_appliquee is None:
                        vitesse_appliquee = self.derniere_vitesse_commande if self.derniere_vitesse_commande is not None else self.vitesse_mode_temperature

                debit = self.debit_pompe(vitesse_appliquee)
'''
if old not in text:
    raise SystemExit("temperature running block not found")
text = text.replace(old, new, 1)
control.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Example configuration.
# ---------------------------------------------------------------------------
example = Path("examples/filtration_piscine.yaml")
text = example.read_text(encoding="utf-8")
anchor = '''  # Pump / variable-speed drive (for example provided by an Aquagem integration)
  cde_pompe: fan.pool_pump
  fan_variateur_pompe: fan.pool_pump

'''
insert = '''  # Pump / variable-speed drive (for example provided by an Aquagem integration)
  cde_pompe: fan.pool_pump
  fan_variateur_pompe: fan.pool_pump

  # Optional local-panel handoff exposed by the pump integration.
  # Température / Marche Forcée: assist ON; Pool Manager sends the initial
  # target once, then the local panel may take over after the integration's
  # quiet timer expires. Intelligent / Hors Gel / Arrêt Forcé: assist OFF so
  # automatic/safety control remains authoritative.
  entity_pompe_local_panel_assist: switch.pool_pump_local_panel_assist
  entity_pompe_local_control_available: binary_sensor.pool_pump_local_panel_control_available
  entity_pompe_local_control_remaining: sensor.pool_pump_time_until_local_control

'''
if anchor not in text:
    raise SystemExit("example pump anchor not found")
text = text.replace(anchor, insert, 1)
example.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Changelog.
# ---------------------------------------------------------------------------
changelog = Path("CHANGELOG.md")
text = changelog.read_text(encoding="utf-8")
header = "# Changelog\n\n"
if not text.startswith(header):
    raise SystemExit("Unexpected changelog header")
section = '''## 0.4.6 - Mode-aware local pump control\n\n- Revert the v0.4.5 last-command pump-speed ownership experiment after production feedback showed unexpected speed behavior, including an observed 100% / 2900 rpm condition.\n- Restore normal automatic speed regulation against the physical `fan.percentage` reported by Home Assistant.\n- Add optional pump-integration local-panel entities. `Température` and `Marche Forcée` enable local-control assist, while `Intelligent`, `Hors Gel` and `Arrêt Forcé` disable it so automatic and safety control remain authoritative.\n- In `Température`, send the configured speed once at mode/start initialization instead of rewriting it on every periodic evaluation; after the pump integration quiet timer expires, a user may change speed locally without Pool Manager immediately overwriting it.\n- Keep `Marche Forcée` non-intrusive after startup: it keeps the running speed and allows the local panel to take over when available.\n- Keep startup, PAC minimum-flow, freeze protection and explicit safety commands authoritative.\n- The local-panel integration entities are optional; installations without them keep previous generic fan behavior.\n\n'''
changelog.write_text(header + section + text[len(header):], encoding="utf-8")


# ---------------------------------------------------------------------------
# Regression tests.
# ---------------------------------------------------------------------------
test = Path("tests/test_pump_command_policy.py")
test.write_text('''import datetime\nimport sys\nfrom pathlib import Path\n\nMODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"\nsys.path.insert(0, str(MODULE_DIR))\n\nfrom pool_common import TAB_MODE\nfrom pool_devices import DevicesMixin\n\n\nclass FakeDevices(DevicesMixin):\n    args = {\n        "fan_variateur_pompe": "fan.pool",\n        "mode_de_fonctionnement": "input_select.pool_mode",\n    }\n    delta_vitesse_min = 5\n    tempo_changement_vitesse = 30\n    pas_vitesse_max = 10\n    entity_pompe_local_panel_assist = "switch.local_assist"\n\n    def __init__(self, current=50, last_command=70, mode=TAB_MODE[1], assist="on"):\n        self.current = current\n        self.derniere_vitesse_commande = last_command\n        self.last_changement_vitesse = datetime.datetime.now() - datetime.timedelta(seconds=60)\n        self.mode = mode\n        self.assist = assist\n        self.calls = []\n\n    def get_state(self, entity_id, attribute=None):\n        if entity_id == "fan.pool" and attribute == "percentage":\n            return self.current\n        if entity_id == "input_select.pool_mode":\n            return self.mode\n        if entity_id == "switch.local_assist":\n            return self.assist\n        return None\n\n    def call_service(self, service, **kwargs):\n        self.calls.append((service, kwargs))\n\n    def maj_electrolyseur(self):\n        pass\n\n    def log(self, *args, **kwargs):\n        pass\n\n    def arret_force_actif(self):\n        return False\n\n\ndef test_automatic_control_uses_physical_speed_again():\n    devices = FakeDevices(current=50, last_command=70, mode=TAB_MODE[1])\n\n    result = devices.set_pump_percentage(70)\n\n    assert result == 60\n    assert devices.calls == [\n        ("fan/set_percentage", {"entity_id": "fan.pool", "percentage": 60})\n    ]\n\n\ndef test_unchanged_physical_speed_is_not_rewritten():\n    devices = FakeDevices(current=70, last_command=70, mode=TAB_MODE[1])\n\n    result = devices.set_pump_percentage(70)\n\n    assert result == 70\n    assert devices.calls == []\n\n\ndef test_temperature_and_forced_modes_enable_local_assist():\n    for mode in (TAB_MODE[0], TAB_MODE[3]):\n        devices = FakeDevices(mode=mode, assist="off")\n        devices.sync_local_panel_policy(mode)\n        assert devices.calls == [\n            ("switch/turn_on", {"entity_id": "switch.local_assist"})\n        ]\n\n\ndef test_intelligent_and_safety_modes_disable_local_assist():\n    for mode in (TAB_MODE[1], TAB_MODE[2], TAB_MODE[4]):\n        devices = FakeDevices(mode=mode, assist="on")\n        devices.sync_local_panel_policy(mode)\n        assert devices.calls == [\n            ("switch/turn_off", {"entity_id": "switch.local_assist"})\n        ]\n\n\ndef test_temperature_path_only_forces_initial_speed_once():\n    source = (MODULE_DIR / "pool_control.py").read_text(encoding="utf-8")\n    assert "elif not self.mode_speed_initialized:" in source\n    assert "if not self.mode_speed_initialized:" in source\n    assert "vitesse_appliquee = self.get_fan_percentage()" in source\n''', encoding="utf-8")
