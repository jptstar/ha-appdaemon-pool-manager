# Roadmap

## 0.1.x - Safe baseline

- Preserve current behavior while fixing hard safety/consistency bugs.
- Keep Home Assistant automations for pool mode selection, intelligent heat-pump enable/disable, and heating override.
- Keep WAGO/CODESYS backwash and hydraulic safety logic outside this project.

## 0.2.x - Clean heating override boundary

- Add an optional heating-override input to Pool Manager.
- Let Home Assistant own the override timer and heat-pump mode/preset.
- Remove direct Aquagem pump commands from the Home Assistant heating-override automation.
- Let Pool Manager become the only component that commands pump on/off and speed during automatic operation.

## 0.3.x - Decision engine cleanup

- Formalize daily `target / completed / remaining` filtration state.
- Improve restart recovery and decision diagnostics.
- Add a dry-run/observer mode for side-by-side validation before enabling pump control.
