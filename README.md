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
                                                  +--> daylight / solar optimization
                                                  +--> heat-pump flow priority
                                                  +--> optional PAC Heat/Off policy
                                                  +--> freeze / fail-safe safety
                                                  +--> chlorination coordination
```

Home Assistant remains a good place for high-level user policy such as automatic pool-mode selection and temporary heating overrides. Pool Manager can optionally take ownership of the normal seasonal PAC `Heat`/`Off` decision so the required pump circulation can be established **before** the PAC is started. Backwash and critical hydraulic valve/pressure safety logic should remain in the PLC/controller when applicable.

## HACS layout

This repository follows the HACS AppDaemon repository layout:

```text
apps/
  pool_manager/
    filtration_piscine.py   # AppDaemon entry point
    pool_common.py          # shared helpers and filtration math
    pool_status.py          # status / quota presentation
    pool_safety.py          # PAC sequencing, freeze and fail-safe layer
    pool_daylight.py        # daylight window + thermal-reference memory
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

The migration to a single pump-control authority is staged. Do **not** remove existing Home Assistant pool/PAC automations simply because the app has been installed.

`gestion_pac_auto` and `hors_gel_adaptatif` are disabled by default. First configure and validate the new safety inputs. When `gestion_pac_auto: true` is enabled, disable any separate Home Assistant automation that performs the same normal seasonal PAC `Heat`/`Off` regulation, otherwise the two controllers can fight each other. A dedicated temporary heating-override automation can remain because the override boundary is explicitly supported.

## Automatic PAC management

Automatic PAC takeover is opt-in:

```yaml
gestion_pac_auto: true
entity_temperature_exterieure: sensor.outdoor_temperature
entity_temperature_exterieure_moyenne_24h: sensor.outdoor_temperature_mean_24h
entity_temperature_exterieure_moyenne_7j: sensor.outdoor_temperature_mean_7d
heure_debut_pac_auto: "08:00:00"
heure_fin_pac_auto: "20:00:00"
```

The default warm-weather start conditions reproduce the existing production policy:

```text
ambient > 25 °C
24 h mean > 19 °C
7 d mean > 18 °C
```

The default cold-weather stop conditions are:

```text
ambient < 21 °C
24 h mean < 18 °C
7 d mean < 17 °C
```

An automatic start is deliberately sequenced:

```text
heating policy becomes eligible
        -> pump ON
        -> pump percentage >= PAC minimum confirmed
        -> climate -> Heat
```

If circulation cannot be confirmed within `pac_auto_timeout_demarrage_s` (30 s by default), the PAC is **not** started. The weather inputs are checked again immediately before the `Heat` command.

A normal automatic stop is sequenced in the opposite safety order:

```text
climate -> Off
        -> post-circulation (60 s by default)
        -> normal Pool Manager pump strategy resumes
```

The PAC remains off outside the configured automatic window unless a temporary Home Assistant heating override is active.

## Heating override boundary

Pool Manager can consume an optional Home Assistant heating-override policy signal:

```yaml
entity_derogation_chauffage: input_boolean.pool_heating_override
```

The override automation may remain responsible for its timer and temporary PAC preset/mode. Pool Manager interprets the active override as a circulation demand and guarantees the same minimum-flow path. Automatic seasonal PAC policy does not fight an active override.

## Fail-safe entity handling

The general fail-safe layer is enabled by default:

```yaml
fail_safe_active: true
```

Important degraded behaviors are intentionally conservative:

- unavailable pool-water temperature never silently becomes 10 °C; Pool Manager uses the configured HA memory helper, then the last valid value, then `temperature_eau_secours_c` (32 °C by default);
- an unavailable PAC climate entity blocks a new automatic PAC start;
- if PAC power proves that the PAC is active while the climate state is unavailable, Pool Manager keeps a PAC circulation demand instead of assuming the PAC is off;
- if PAC power indicates activity but pump state/speed cannot confirm the configured minimum circulation, Pool Manager first tries to restore circulation, then commands the PAC off after `pac_flow_fail_timeout_s` when the climate entity is controllable;
- loss of one of the three PAC weather inputs blocks new starts immediately; a running automatic PAC can use the last complete weather snapshot for the configured grace period, then is switched off if the sensor fault persists;
- fail-safe state changes are logged to `piscine_log` without adding another Home Assistant helper.

`Arrêt Forcé` remains an explicit highest-priority user command.

## Adaptive freeze protection

The existing periodic `Hors Gel` circulation remains available. Optional adaptive protection adds a second safety layer:

```yaml
hors_gel_adaptatif: true
hors_gel_continu_on_c: 1.0
hors_gel_continu_off_c: 3.0
```

Behavior with the defaults:

```text
outdoor temperature <= 1 °C
    -> continuous circulation at the minimum safe speed

1 °C < temperature < 3 °C
    -> hysteresis: keep the previous freeze-safety state

outdoor temperature >= 3 °C
    -> release continuous protection
    -> if operating mode is Hors Gel, resume periodic circulation
```

A valid temperature at/below the low threshold can activate protection even if the Home Assistant mode-selection automation failed to switch the selector to `Hors Gel`. If the outdoor-temperature entity itself is unavailable while the operating mode is already `Hors Gel`, Pool Manager also falls back to continuous circulation. Chlorination is disabled during continuous freeze protection and the PAC is commanded off.

## Daylight-aware Intelligent mode

`Intelligent` mode can now follow the real daylight period instead of treating 09:00-18:00 as the biological/solar day all year:

```yaml
suivre_soleil_reel: true
marge_apres_lever_soleil_min: 0
marge_avant_coucher_soleil_min: 0
```

When AppDaemon sun information is available, sunrise starts the preferred filtration period and sunset ends it. The existing `heure_debut_solaire` / `heure_fin_solaire` values remain an automatic fallback. The expected daily quota is paced over this daylight window, and a large electrical export cannot by itself start the pump before sunrise. Evening catch-up begins after the real sunset and retains the configured catch-up deadline.

The water-temperature helper `mem_temp` is also used as a **thermal reference** while the pump is stopped. This avoids running the pump at night merely to obtain a representative temperature:

```yaml
temperature_reference_lissee: true
temperature_reference_alpha: 0.35
temperature_reference_min_update_s: 1800
brassage_nuit_intelligent: false
```

While the pump is stopped, the daily target starts from this persisted representative temperature. Once circulation has run for the existing `tempo_eau` delay, the live target immediately uses the real water probe and can therefore correct itself for the current day. The persisted memory is refreshed progressively from valid physical measurements only; fail-safe substituted values are never averaged into it.

`brassage_nuit_intelligent: false` only suppresses periodic night mixing in `Intelligent` mode. It does **not** disable `Hors Gel`: periodic freeze circulation and the optional continuous freeze fail-safe remain completely independent.

## Adaptive filtration target

When `mode_calcul` is enabled, Pool Manager uses a continuous adaptive curve instead of the former hot-water polynomial:

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

Pool Manager does not assume that a percentage of pump speed is itself a percentage of hydraulic filtration. Equivalent filtration is accumulated from the **relative estimated flow**:

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