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
    pool_predictive.py      # weather scoring + legacy trajectory planner
    pool_mpc.py             # adaptive thermal model + receding-horizon MPC
    pool_predictive_runtime.py # forecast cache, persistent thermal learning + HA diagnostics
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

## Adaptive predictive heating — Thermal Model + MPC

With `chauffage_predictif: true`, **Automatique** and **Fin de saison • Smart** use the same self-learning thermal engine. Since v0.8, the default planner is a **receding-horizon Model Predictive Controller (MPC)** driven by an adaptive thermal model learned from the real pool. It still exposes one clear current action:

```text
WAIT       -> do not heat
PRESERVE   -> protect recoverability only
PREHEAT    -> heat today to the thermal trajectory target
MAINTAIN   -> selected bathing day: recover/hold the requested water target
```

The weather outlook remains visible up to 15 days, but a distant forecast does not trigger immediate heating. Pool Manager simulates future OFF / Smart / Turbo strategies against the learned pool physics, estimates their energy cost, and repeatedly chooses the lowest-energy reachable trajectory. The optimization is recalculated whenever the weather, certified water temperature or learned model changes.

```yaml
chauffage_predictif: true
chauffage_predictif_mpc: true
entity_meteo_chauffage_predictif: weather.home
entity_chauffage_predictif_status: sensor.pool_predictive_heating
chauffage_predictif_horizon_jours: 15

chauffage_predictif_temperature_baignade_min_c: 21
chauffage_predictif_temperature_baignade_ideale_c: 26
chauffage_predictif_score_baignade_min: 55

# Fallbacks while the installation is still learning.
chauffage_predictif_gain_chauffe_c_par_h: 0.30
chauffage_predictif_perte_nuit_delta10_c_par_h: 0.05

chauffage_predictif_apprentissage: true

# MPC defaults. Usually no tuning is required.
chauffage_predictif_mpc_pas_h: 1.0
chauffage_predictif_mpc_pas_temperature_c: 0.2
chauffage_predictif_mpc_puissance_smart_w: 1200
chauffage_predictif_mpc_puissance_turbo_w: 1900

# Pool-water stabilization is shared with normal filtration:
# tempo_eau is the delay after a meaningful pump stop.
# 70% is the minimum speed for a certified physical temperature sample,
# but predictive sampling never commands that speed.
chauffage_predictif_mesure_vitesse_pct: 70

# Optional absolute recoverability floor:
# chauffage_predictif_temperature_min_eau_c: 22
```

### Certified water temperature

A pipe sensor is not treated as the pool merely because the pump has just started. Pool Manager now uses the same hydraulic stabilization already used by normal filtration: after a meaningful stop, the probe is ignored until `tempo_eau` has elapsed. Short pump interruptions do not re-arm a full stabilization cycle.

Predictive sampling is passive. It never starts an autonomous pump cycle and never overrides pump speed just to obtain a temperature. Once normal circulation has stabilized, the physical water probe is certifiable only when the real pump speed is at least `chauffage_predictif_mesure_vitesse_pct` (**70% by default**). At 47–69%, the reading can still serve normal filtration logic, but it is not accepted as a certified MPC/learning sample. `mem_temp` remains the operational fallback while the pump is stopped and is never accepted as a new learning sample.

If predictive heating genuinely needs to start while the pump is off, circulation may be started because heating itself requires flow. The controller then waits for the normal `tempo_eau` stabilization before using the physical probe for the final heating decision.

### What it learns

The persistent thermal model learns from real certified measurements:

- net water-temperature gain in °C/h by PAC preset (Smart, Turbo, etc.) and outdoor-temperature range;
- average PAC electrical power for the same learned samples, including derived kWh/°C when available;
- passive overnight loss in °C/h from two certified pool measurements, water/air temperature difference and real cover state;
- only clean samples are accepted: PAC activity contaminates a passive-loss sample, and a cover-state change rejects it.

For night-loss learning, the start point is the **last certified pool temperature before the long stop**, and the end point is the **first new certified pool temperature after circulation has mixed the pool again**. Stale pipe values and `mem_temp` do not enter that calculation.

The model is persisted to `pool_manager_thermal_learning.json` outside the HACS-managed package by default, so AppDaemon restarts and HACS updates keep the learned behavior.

### Weather and actual use

Daily weather keeps the strategic 15-day view. When the configured weather provider supports hourly forecasts, Pool Manager also refines near-term decisions with hourly conditions:

- weekdays prioritize the normal after-work bathing window, approximately **16:00-20:00**;
- Saturdays and Sundays receive additional usage priority and use a broader daytime window;
- PAC capacity uses near-term average daytime temperature instead of blindly using the daily maximum.

A marginal earlier day is therefore not automatically chosen when a much better nearby weekend or late-afternoon window exists. A candidate must also be physically recoverable with the learned PAC performance.

### Adaptive Thermal Model + MPC

The adaptive model predicts the pool response from the data already learned by Pool Manager: PAC gain by preset/outdoor-temperature range, PAC power, passive losses, water/air delta and cover state. During a cold start, learned values are blended with conservative fallback values; as certified samples accumulate, the real installation progressively dominates the model.

For every credible bathing opportunity, MPC simulates future choices such as:

```text
OFF
Smart for 1 h, 2 h, ...
Turbo for 1 h, 2 h, ...
```

The controller minimizes predicted PAC energy while respecting the absolute water floor and the bathing target. Turbo carries a small optimization penalty, and night heating carries a much larger one, so both are used only when they materially improve reachability.

This makes the old fixed trajectory concept dynamic. The dashboard attribute `adaptive_floor_temperature` is the minimum water temperature that still keeps the selected MPC plan feasible from the current state. It can therefore be well above the configured absolute floor when recovery capacity is tight, and close to the absolute floor when several efficient heating opportunities remain.

Example:

```text
Absolute floor       : 22.0 °C
Adaptive floor today : 25.4 °C
Water now            : 26.1 °C
MPC choice           : WAIT

Tomorrow is warmer and learned Smart efficiency is better
-> keep PAC off today
-> use the cheaper warm window tomorrow
```

The MPC plan is **receding horizon**: its multi-day schedule is a forecast, not a rigid timer. Pool Manager executes only the current decision, then recalculates after new weather or a new certified pool measurement. Hydraulic safety, forced stop, freeze protection and PAC minimum flow remain deterministic and always have priority over MPC.

### Summer, autumn and bad weather

The same engine naturally behaves differently by season. In summer, frequent good weather, warm air and small losses tend to keep the trajectory close to the normal water target with little PAC work. If several poor days arrive, the PAC can stop instead of maintaining full temperature unnecessarily.

Near autumn, larger losses and lower PAC performance make full-time 30 °C maintenance expensive. Pool Manager then targets useful bathing days, allows the pool to cool between them, and preserves only enough thermal reserve to keep the next useful window recoverable.

An optional absolute floor can still be configured:

```yaml
chauffage_predictif_temperature_min_eau_c: 22
```

Above that floor and without a useful bathing window, the PAC stays off. Forecast losses can trigger a small PRESERVE action before the pool becomes difficult to recover.

The real cover entity is used for learning. Future overnight forecasts use `chauffage_predictif_volet_nuit_prevu` (default `closed` when a cover entity is configured), so an open daytime cover does not make the planner assume that every future night will also be open.

`brassage_nuit_intelligent: false` remains independent: it disables periodic night mixing, but it does not prohibit an exceptional pump + PAC recovery when the thermal trajectory genuinely requires it.

### Pool Manager log sensor

Pool Manager mirrors every event written to the dedicated `piscine_log` AppDaemon log into a virtual Home Assistant entity. By default it is:

```yaml
entity_pool_manager_log: sensor.pool_manager_log
```

No Home Assistant helper is required. The entity state contains the latest event; attributes expose its timestamp, category, complete message and the last 20 events in `history`.

### Dashboard sensor

If `entity_chauffage_predictif_status` is configured, useful attributes now include:

- `action`
- `water_temperature_estimated`
- `certified_water_temperature`
- `certified_water_at`
- `measurement_active`
- `measurement_reference_speed_pct` (70% by default; validity threshold only)
- `current_cover`
- `next_swim_date`
- `next_swim_usage_score`
- `next_swim_weekend`
- `recovery_start_date`
- `planner` (`MPC` by default in v0.8)
- `model_confidence`
- `adaptive_floor_temperature`
- `trajectory_target_temperature`
- `recommended_preset`
- `night_heating`
- `night_required_c`
- `thermal_margin_c` (current water minus adaptive floor in MPC mode)
- `required_gain_c`
- `predicted_night_loss_c`
- `mpc_energy_kwh`
- `mpc_night_energy_required`
- `mpc_plan`
- `learned_heating_rates`
- `learned_night_losses`
- `forecast`

## Aquagem / iSaver local-control handover

Aquagem Pump 0.4.2+ can intentionally return authority to the physical panel after a protected bus-silence window. That is useful in manual-capable pool modes, but an automatic Pool Manager mode must not release authority or the pump can jump back to the panel's remembered speed (for example Max / 2900 rpm).

Pool Manager v0.8.1 therefore enforces this policy:

```text
Intelligent   -> remote Pool Manager authority
Hors Gel      -> remote Pool Manager authority
Température   -> local-panel handover allowed
Marche Forcée -> local-panel handover allowed
Arrêt Forcé   -> remote/safety authority
```

When the Aquagem `Retour au contrôle local` switch is configured with `entity_pompe_local_panel_assist`, Pool Manager disables it automatically in Intelligent/Hors Gel and self-heals it if it is turned back on. In addition, v0.8.1 sends a short remote keepalive before the iSaver watchdog can hand control back. This keepalive works even when the switch entity was omitted for known Aquagem/iSaver fan entity ids.

Recommended configuration:

```yaml
entity_pompe_local_panel_assist: switch.pool_pump_return_to_local_control
entity_pompe_local_control_available: binary_sensor.pool_pump_local_control_available
entity_pompe_local_control_remaining: sensor.pool_pump_time_until_local_control

pompe_remote_keepalive: true
pompe_remote_keepalive_s: 30
```

For non-Aquagem fan integrations, the keepalive is disabled unless explicitly enabled. The automatic keepalive never runs in Température or Marche Forcée, so intentional manual/local speed control remains possible.

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