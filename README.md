# Pool Manager for AppDaemon

Pool Manager is an AppDaemon application for advanced swimming-pool filtration orchestration in Home Assistant.

It is deliberately **not a hardware integration**. Device protocols remain handled by dedicated Home Assistant integrations (for example an Aquagem variable-speed pump integration or an AstralPool/heat-pump integration). Pool Manager consumes their Home Assistant entities and coordinates filtration, solar surplus, heat-pump flow requirements, daily filtration quota, freeze protection, night circulation and chlorination.

## Architecture

```text
Aquagem / pump integration ----\
AstralPool / heat pump ---------+--> Home Assistant entities --> Pool Manager (AppDaemon)
Shelly / energy ----------------/
                                                  |
                                                  +--> pump on/off + speed
                                                  +--> filtration quota
                                                  +--> solar optimization
                                                  +--> heat-pump flow priority
                                                  +--> chlorination coordination
```

Home Assistant automations remain a good place for high-level user policy such as automatic pool-mode selection, seasonal heat-pump enable/disable and temporary heating overrides. Backwash and critical hydraulic safety logic should remain in the PLC/controller when applicable.

## HACS layout

This repository follows the HACS AppDaemon repository layout:

```text
apps/
  pool_manager/
    filtration_piscine.py   # AppDaemon entry point
    pool_common.py          # shared helpers and filtration math
    pool_status.py          # status / quota presentation
    pool_lifecycle.py       # startup, listeners and scheduling
    pool_devices.py         # pump, PAC and chlorinator entity handling
    pool_strategy.py        # quota / solar / priority strategy
    pool_control.py         # main decision and control loop
```

HACS installs the whole `pool_manager` directory. Your AppDaemon `app_dir` must point to the directory used by HACS for AppDaemon apps.

## Installation

1. Install and configure AppDaemon with Home Assistant.
2. In HACS options, enable **AppDaemon apps discovery & tracking**.
3. Add `jptstar/ha-appdaemon-pool-manager` as a custom **AppDaemon** repository.
4. Download Pool Manager.
5. Copy [`examples/filtration_piscine.yaml`](examples/filtration_piscine.yaml) into your own AppDaemon configuration area and replace the placeholder entity IDs with your Home Assistant entities.
6. Reload/restart AppDaemon and inspect the logs before allowing the app to control production equipment.

Keep your personal `filtration_piscine.yaml` outside the HACS-managed Pool Manager directory so HACS updates cannot overwrite your site-specific configuration. `examples/apps.yaml` is kept for compatibility, but `examples/filtration_piscine.yaml` is the canonical complete configuration template.

## Important migration note

The migration to a single pump-control authority is staged. Do **not** remove existing Home Assistant pool/PAC automations simply because the app has been installed. Heating-override pump commands should only be removed after the optional override input below has been configured and validated in production.

## Heating override boundary

Pool Manager can consume an optional Home Assistant heating-override policy signal:

```yaml
entity_derogation_chauffage: input_boolean.pool_heating_override
```

Home Assistant remains responsible for the override timer and for turning/configuring the heat pump. Pool Manager does not change the heat-pump protocol or preset; it interprets the active override as a PAC circulation demand. In Intelligent mode this enters the same PAC-priority path as a normal detected heating demand, including the existing PAC/solar/quota coordination.

If `entity_derogation_chauffage` is not configured, behavior is unchanged from the previous release.

## Adaptive filtration target

When `mode_calcul` is enabled, Pool Manager now uses a continuous adaptive curve instead of the former hot-water polynomial:

```text
T <= 25 °C : target = T / 2
T > 25 °C  : target = 12.5 × exp(0.05 × (T - 25))
```

The two branches meet continuously at **12.5 equivalent hours at 25 °C**. The familiar temperature/2 rule is therefore preserved in cool and moderate water, while warm water progressively receives more filtration without the very aggressive rise of the historical polynomial.

Examples before applying the existing filtration coefficient:

| Water temperature | Adaptive target |
| ---: | ---: |
| 20 °C | 10 h |
| 24 °C | 12 h |
| 25 °C | 12 h 30 |
| 28 °C | ~14 h 31 |
| 30 °C | ~16 h 03 |
| 31.1 °C | ~16 h 57 |
| 34 °C | ~19 h 36 |

The existing daily **24 h** cap remains a hard safety limit.

## Equivalent filtration and hydraulic estimate

Pool Manager does not assume that a percentage of pump speed is itself a percentage of hydraulic filtration. Equivalent filtration is now accumulated from the **relative estimated flow**:

```text
equivalent hours = real runtime × estimated current flow / estimated reference flow
```

With the example model (`47% ≈ 8 m³/h`, reference `70% ≈ 11.47 m³/h`, `100% ≈ 16 m³/h`), one real hour contributes approximately:

```text
47%  -> 0.70 equivalent h
70%  -> 1.00 equivalent h
100% -> 1.39 equivalent h
```

The `entity_volume_filtre_jour` value remains an **estimate**, because there is no physical flow-meter. Pool Manager deliberately uses the relative flow ratio for quota decisions rather than claiming that the calculated m³ value is a measured volume. If a real flow sensor is added in the future, the hydraulic layer can be upgraded without changing the high-level filtration strategy.

## Daily filtration limit

The daily equivalent filtration target is capped to **24 hours**. The same capped value is used for:

- the displayed filtration duration;
- the daily quota target;
- the calculated filtration schedule.

A 24-hour target is represented as a full-day window rather than creating negative or next-day clock values.

## Status and quota

Pool Manager reuses the two existing Home Assistant status helpers; no additional quota helper is required.

`message_filtration_piscine` contains the current decision, for example:

```text
Attente surplus solaire
```

`message_filtration_piscine_detail` starts with the daily equivalent-filtration state and then keeps the useful operating details:

```text
Besoin 14 h 30 | Effectué 6 h 12 | Restant 8 h 18 | 47% | 8.0m3/h | limite 22:00
```

Legacy `x.y/z.yh` progress fragments are removed from the detailed line because the same information is already shown as need/completed/remaining.

## Restart behavior

Reloading the AppDaemon app no longer unconditionally switches the pump off. Pool Manager first resumes the real Home Assistant pump state and current speed when available, then lets the normal decision logic determine the next command.

## Device integrations

Pool Manager expects ordinary Home Assistant entities and does not depend on a specific wire protocol.

Typical entity types:

- variable-speed pump: `fan` with `turn_on`, `turn_off` and percentage support;
- heat pump: `climate`;
- chlorinator production: `number`;
- pool cover: `cover`;
- power/energy data: `sensor`;
- user settings/status: Home Assistant helpers such as `input_number`, `input_boolean`, `input_select`, `input_datetime` and `input_text`.

Aquagem and AstralPool remain external Home Assistant integrations. Pool Manager only orchestrates their entities.

## Configuration

Use [`examples/filtration_piscine.yaml`](examples/filtration_piscine.yaml) as the canonical complete template. Entity IDs in the example are placeholders and must be replaced with entities from your Home Assistant instance. Keep the resulting personal YAML outside the HACS-managed app directory.

## License

GNU General Public License v3.0 (`GPL-3.0-only`).

Copyright (C) 2026 jptstar.
