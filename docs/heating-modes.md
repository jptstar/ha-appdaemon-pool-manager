# Heating modes

From v0.4.4, Pool Manager can optionally own all user-facing heat-pump policy through one Home Assistant selector.

## Home Assistant helpers

```yaml
input_select:
  pool_heating:
    name: Pool heating
    options:
      - "Désactivé"
      - "Automatique"
      - "Première chauffe • Smart"
      - "Fin de saison • Smart"
      - "Turbo • 1 h"
      - "Turbo • 2 h"
      - "Turbo • 3 h"
      - "Turbo • 6 h"
      - "Turbo • 12 h"
      - "Turbo • 1 jour"
      - "Turbo • 2 jours"
      - "Turbo • 3 jours"
    icon: mdi:heat-pump

input_text:
  pool_heating_previous:
    name: Previous pool heating mode
    max: 64

timer:
  pool_heating:
    name: Pool heating Turbo timer
    restore: true
```

Do not set an `initial` value on the selector if you want Home Assistant to restore the last selected strategy after a restart.

## AppDaemon configuration

```yaml
entity_chauffage: input_select.pool_heating
entity_chauffage_timer: timer.pool_heating
entity_chauffage_precedent: input_text.pool_heating_previous

chauffage_preset_auto: Smart
chauffage_preset_smart: Smart
chauffage_preset_turbo: Turbo
chauffage_premiere_chauffe_marge_c: 0.3
```

## Strategy behavior

`Désactivé` commands the Pool Manager-controlled PAC off. PAC flow fail-safe protection remains available if measured PAC power proves that the unit is still running.

`Automatique` keeps the existing seasonal policy: the configured time window and outdoor / 24 h / 7 d temperature thresholds decide when Pool Manager may place the PAC in `Heat`. If `entity_mode_auto` is configured, this strategy also respects that master automatic-mode boolean. `Smart` is restored as the normal preset after a Turbo mode.

`Première chauffe • Smart` is an explicit user command. Pool Manager establishes the minimum pump flow first, then requests `Heat` and `Smart`. The seasonal weather and time-window gate is bypassed. Once the measured water temperature reaches the climate target minus `chauffage_premiere_chauffe_marge_c`, the selector automatically returns to `Automatique`. If water temperature or the climate target is temporarily unavailable, Pool Manager does not falsely declare first heat complete.

`Fin de saison • Smart` is also explicit. It bypasses the normal seasonal weather and 08:00-20:00 gate, requests `Heat/Smart` and keeps the PAC minimum circulation demand continuously. The heat pump's own thermostat remains responsible for cycling the compressor around the climate setpoint. This lets a pool maintain its requested temperature through cool nights without requiring a second Home Assistant heating automation.

The Turbo modes request `Heat/Turbo`, establish the same minimum circulation first, start the configured Home Assistant timer, and restore the previous non-Turbo heating strategy when the timer finishes. The optional `input_text` helper persists that previous strategy across AppDaemon reloads. If no HA timer is configured, Pool Manager falls back to an in-memory AppDaemon timer.

## Priority order

The heating selector does not bypass hydraulic safety. The effective priority remains:

```text
Arrêt Forcé
  -> critical adaptive Hors Gel / freeze protection
  -> PAC flow fail-safe
  -> explicit heating strategy (Turbo / first heat / end of season)
  -> normal automatic PAC policy
  -> Intelligent filtration / solar / quota arbitration
```

The legacy `entity_derogation_chauffage` boolean remains supported for backwards compatibility. A new installation using `entity_chauffage` normally does not need the old Home Assistant PAC override automation.