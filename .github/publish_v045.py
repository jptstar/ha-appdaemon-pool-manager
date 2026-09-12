from pathlib import Path
import textwrap


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"Expected exactly one match in {path!r}, found {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


devices_old = textwrap.dedent('''\
    def set_pump_percentage(self, percentage, force=False):
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
''')

devices_new = textwrap.dedent('''\
    def set_pump_percentage(self, percentage, force=False):
        percentage = int(max(0, min(100, percentage)))
        now = datetime.datetime.now()

        current = self.get_fan_percentage()
        last_command = self.derniere_vitesse_commande

        # Normal regulation compares the requested target with the last
        # command emitted by Pool Manager, not with a manual fan change.
        # This lets a user temporarily take over the pump speed while the
        # automatic target itself remains unchanged.
        if not force and last_command is not None:
            if abs(percentage - last_command) < self.delta_vitesse_min:
                return current if current is not None else last_command

        if not force and (now - self.last_changement_vitesse).total_seconds() < self.tempo_changement_vitesse:
            if current is not None:
                return current
            return last_command if last_command is not None else percentage

        # Once the automatic target really changes, resume from the physical
        # speed reported by Home Assistant so the existing ramp limit remains
        # smooth even after a manual adjustment.
        reference = current
        if reference is None:
            reference = last_command
        if reference is None:
            reference = percentage

        if not force:
            if percentage > reference:
                percentage = min(reference + self.pas_vitesse_max, percentage)
            elif percentage < reference:
                percentage = max(reference - self.pas_vitesse_max, percentage)

        try:
            self.call_service("fan/set_percentage", entity_id=self.args["fan_variateur_pompe"], percentage=percentage)
            self.derniere_vitesse_commande = percentage
            self.last_changement_vitesse = now
            self.maj_electrolyseur()
            return percentage
        except Exception as e:
            self.log(f"⚠️ Erreur set_percentage : {e}", log="piscine_log")
            return current
''')

replace_once("apps/pool_manager/pool_devices.py", devices_old, devices_new)

control = Path("apps/pool_manager/pool_control.py")
control_text = control.read_text(encoding="utf-8")
forced = "self.set_pump_percentage(self.vitesse_mode_temperature, force=True)"
if control_text.count(forced) != 2:
    raise SystemExit(f"Expected two forced temperature writes, found {control_text.count(forced)}")
control.write_text(
    control_text.replace(forced, "self.set_pump_percentage(self.vitesse_mode_temperature)"),
    encoding="utf-8",
)

changelog = Path("CHANGELOG.md")
changelog_text = changelog.read_text(encoding="utf-8")
header = "# Changelog\n\n"
if not changelog_text.startswith(header):
    raise SystemExit("Unexpected CHANGELOG header")
section = textwrap.dedent('''\
    ## 0.4.5 - Manual pump speed handoff

    - Send normal pump-speed commands only when Pool Manager's requested target changes by at least the configured delta, instead of repeatedly rewriting the currently expected speed.
    - Compare normal targets with the last speed command emitted by Pool Manager, so an unchanged automatic target does not immediately overwrite a manual speed adjustment made through Home Assistant or the pump integration.
    - When the automatic target really changes, resume control from the physical `fan.percentage` reported by Home Assistant and keep the existing ramp and minimum-delay limits.
    - Stop forcing repeated speed writes from the normal `Température` filtration path during periodic re-evaluation.
    - Keep forced speed commands for delayed pump startup and safety paths such as PAC minimum flow and freeze protection.
    - No YAML migration is required.
    - Add regression tests for manual handoff, automatic takeover on target change and forced safety commands.

''')
changelog.write_text(header + section + changelog_text[len(header):], encoding="utf-8")

test = Path("tests/test_pump_command_policy.py")
test.write_text(textwrap.dedent('''\
    import datetime
    import sys
    from pathlib import Path

    MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
    sys.path.insert(0, str(MODULE_DIR))

    from pool_devices import DevicesMixin


    class FakeDevices(DevicesMixin):
        args = {"fan_variateur_pompe": "fan.pool"}
        delta_vitesse_min = 5
        tempo_changement_vitesse = 30
        pas_vitesse_max = 10

        def __init__(self, current=50, last_command=70):
            self.current = current
            self.derniere_vitesse_commande = last_command
            self.last_changement_vitesse = datetime.datetime.now() - datetime.timedelta(seconds=60)
            self.calls = []

        def get_state(self, entity_id, attribute=None):
            if entity_id == "fan.pool" and attribute == "percentage":
                return self.current
            return None

        def call_service(self, service, **kwargs):
            self.calls.append((service, kwargs))

        def maj_electrolyseur(self):
            pass

        def log(self, *args, **kwargs):
            pass


    def test_same_automatic_target_preserves_manual_speed():
        devices = FakeDevices(current=50, last_command=70)

        result = devices.set_pump_percentage(70)

        assert result == 50
        assert devices.calls == []
        assert devices.derniere_vitesse_commande == 70


    def test_new_automatic_target_reclaims_control_from_manual_speed_with_ramp():
        devices = FakeDevices(current=50, last_command=70)

        result = devices.set_pump_percentage(80)

        assert result == 60
        assert devices.calls == [
            ("fan/set_percentage", {"entity_id": "fan.pool", "percentage": 60})
        ]
        assert devices.derniere_vitesse_commande == 60


    def test_force_still_reasserts_safety_speed():
        devices = FakeDevices(current=50, last_command=70)

        result = devices.set_pump_percentage(70, force=True)

        assert result == 70
        assert devices.calls == [
            ("fan/set_percentage", {"entity_id": "fan.pool", "percentage": 70})
        ]


    def test_temperature_mode_does_not_force_periodic_speed_writes():
        source = (MODULE_DIR / "pool_control.py").read_text(encoding="utf-8")
        assert "set_pump_percentage(self.vitesse_mode_temperature, force=True)" not in source
'''), encoding="utf-8")
