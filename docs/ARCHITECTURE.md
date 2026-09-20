# Pool Manager v0.10 architecture

`FiltrationPiscine` is the composition root and the only AppDaemon entry point. Domain components do not override one another. Their initialization stages and cross-domain priority decisions are called explicitly, so changing the Python base-class order cannot change pool behavior.

## Responsibility map

| Domain | Owner | Responsibility | Must not decide |
| --- | --- | --- | --- |
| Filtration | `pool_control.py`, `pool_common.py` | Daily target, operating-mode control flow, protected calibration priority | PAC start policy, MPC optimization |
| PAC policy | `pool_heating.py` | Heating selector, Turbo/debut/end-of-season policy, predictive action execution | Freeze and flow fail-safe |
| MPC | `pool_mpc.py`, `pool_predictive.py` | Pure forecast normalization, thermal simulation and plan optimization | Direct Home Assistant commands |
| Calibration | `pool_predictive_runtime.py` | Certified water measurement lifecycle and thermal-model learning | Normal quota/solar arbitration |
| Energy/quota | `pool_strategy.py`, `pool_daylight_core.py` | Solar window, grid/PV arbitration, quota recovery and pump-speed choice | Safety overrides |
| Safety | `pool_safety.py` | Forced stop, freeze protection, PAC flow, sensor fail-safe, safe sequencing | Comfort optimization |
| Devices | `pool_devices.py` | Home Assistant reads/writes and pump/PAC/chlorinator primitives | High-level policy |
| Journal/status | `pool_journal.py`, `pool_status.py` | Event normalization, deduplication and HA presentation | Equipment decisions |
| Lifecycle | `pool_lifecycle.py`, `pool_runtime_stability.py` | Listeners, schedules, restart recovery and remote-authority keepalive | Domain policy |

## Priority contract

The runtime priority is fixed:

1. forced stop and safety;
2. continuous freeze protection;
3. protected certified-temperature calibration;
4. PAC/MPC circulation demand;
5. daily filtration quota;
6. solar/grid optimization;
7. optional night mixing.

PAC circulation demand is composed explicitly in `FiltrationPiscine.pac_besoin_chauffe`: Home Assistant override, heating/calibration policy, safety/post-circulation, then physical PAC state. Electrolysis permission is composed from cold-water protection, safety mode, and hydraulic conditions.

## Compatibility contract

v0.10 does not rename an AppDaemon YAML key, Home Assistant entity, heating-selector option, virtual sensor, service call, or persisted thermal-learning file. `tests/test_architecture_contract.py` freezes the v0.9.1 canonical entity mapping and rejects duplicate component method ownership or new cooperative `super()` dispatch inside domain code.

## Validation boundary

The repository prepares v0.10.0 but does not publish it. A release tag and GitHub/HACS release must wait for the automated suite, configuration validation, AppDaemon startup smoke test, and production dry-run/observer validation.
