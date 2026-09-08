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
    pool_status.py          # optional structured quota/status output
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
5. Add an app entry to your AppDaemon `apps.yaml` using `examples/apps.yaml` as a starting point.
6. Reload/restart AppDaemon and inspect the logs before allowing the app to control production equipment.

## Important migration note

Version 0.1.x is a safe baseline intended to remain close to the existing production behavior. Do **not** remove existing Home Assistant pool/PAC automations simply because the app has been installed. The heating-override automation still needs a staged migration before Pool Manager can become the only component commanding the pump.

## Daily filtration limit

The daily equivalent filtration target is capped to **24 hours**. The same capped value is used for:

- the displayed filtration duration;
- the daily quota target;
- the calculated filtration schedule.

A 24-hour target is represented as a full-day window rather than creating negative or next-day clock values.

## Structured quota status

An optional Home Assistant `input_text` can expose the quota in a human-readable form without replacing the existing status and detail messages:

```text
BESOIN 14 h 30 | EFFECTUÉ 6 h 12 | RESTANT 8 h 18 | DÉCISION Attente surplus solaire
```

Configure it with:

```yaml
message_filtration_quota: input_text.pool_manager_quota_status
```

A maximum length of **255 characters** is recommended for this helper. The decision label is taken from Pool Manager's current control state, while need/done/remaining use the same daily equivalent-filtration values as the control strategy.

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

See [`examples/apps.yaml`](examples/apps.yaml). Entity IDs in the example are placeholders and must be replaced with entities from your Home Assistant instance.

## License

GNU General Public License v3.0 (`GPL-3.0-only`).

Copyright (C) 2026 jptstar.
