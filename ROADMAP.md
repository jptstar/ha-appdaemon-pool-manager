# Roadmap

## 0.1.x - Safe baseline

- Preserve current behavior while fixing hard safety/consistency bugs.
- Keep Home Assistant automations for pool mode selection, intelligent heat-pump enable/disable, and heating override.
- Keep WAGO/CODESYS backwash and hydraulic safety logic outside this project.

## 0.2.x - Clean heating override boundary

- [x] Add an optional heating-override input to Pool Manager.
- [x] Let Home Assistant own the override timer and heat-pump mode/preset.
- [x] Validate the heating override with Pool Manager as the pump authority in production.
- [x] Make PAC/quota arbitration energy-aware so an impossible quota cannot force indefinite 100% pump speed during heavy grid import.
- [ ] Remove the now-disabled legacy Home Assistant heating-override automation after a longer production validation period.
- [ ] Audit remaining Home Assistant automations for direct Aquagem pump commands.
- [ ] Let Pool Manager become the only component that commands pump on/off and speed during automatic operation.
- [ ] Continue production validation of PAC + solar + quota arbitration.

## 0.3.x - Decision engine cleanup

- [x] Formalize daily `target / completed / remaining` filtration presentation.
- [ ] Improve restart recovery and decision diagnostics.
- [ ] Add a dry-run/observer mode for side-by-side validation before enabling broader pump-control changes.
