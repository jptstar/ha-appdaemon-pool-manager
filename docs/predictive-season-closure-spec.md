# Predictive end-of-season closure — implementation specification

Status: design prepared; implementation and regression tests pending.

## Policy

- Applies to `Fin de saison • Smart` without changing the selected HA mode.
- Before 10 October, keep the 15-day MPC forecast and bathing score in charge of reversible heating pauses.
- A seven-day rolling outdoor mean below 16 °C for 72 consecutive hours is a cold-weather confirmation, not by itself a permanent closure. Permanently close early only when the 15-day forecast has no realistic bathing opportunity (score below configured minimum, default 55) and the MPC cannot economically recover bathing temperature. Do not use a single bad forecast to close irreversibly.
- From 10 October 00:00 local time, permanently close heating for that season, regardless of score or outdoor temperature.
- Persist season-closed state across AppDaemon restarts. Reset only on a configured new-season transition, not on a transient warm forecast.
- Expose states `active`, `paused_weather`, `closed_early`, `closed_date`, `manual_override` and a reason in predictive diagnostics.
- A manual override is time-limited (default 24 hours), never resets permanent closure, and must not bypass pump-flow, forced-stop, freeze-protection, compressor or hydraulic safety.
- Do not alter filtration quota, pump scheduling, freeze-protection, or necessary PAC post-circulation.

## Integration points

1. `pool_heating.py`: enforce seasonal gate in `_manage_pac_auto`, `_request_chauffage_start`, `_check_chauffage_start`, and the final heat command to prevent queued requests bypassing closure.
2. `pool_predictive_runtime.py`: evaluate 15-day forecast, bathing score, water recovery feasibility and existing MPC decision before allowing preservation floor. Keep diagnostics running while closed; stop heating calibration only.
3. `pool_lifecycle.py`: schedule/re-evaluate at midnight and after restart, persist closure in an HA helper or durable state.
4. `examples/apps.yaml`: expose date 10-10, 16 °C, 72 hours, 15-day horizon, and 24-hour manual override as documented configuration.
5. `pool_safety.py`: preserve existing priority for forced stop and freeze circulation.

## Required regression tests

- September: short cold snap -> reversible pause; subsequent sunny weekend -> potential recovery.
- September: 72 h of cold 7-day mean + no viable 15-day bathing window -> persistent early closure.
- September: cold 72 h but viable bathing window -> no permanent closure.
- 9 October 23:59 vs 10 October 00:00 local time; restart on 11 October.
- Closed state blocks Turbo, automatic, preservation, pending delayed start and calibration heat.
- Temporary override expires and does not clear closure; safety interlocks still win.
- Missing/stale forecast and outdoor sensor -> fail safe, no false early permanent closure.
- Filtration quota, frost circulation and post-circulation unchanged.

## Implementation note

Do not equate `end_season` with `disabled`: it is an operator-selected strategy. Current `_manage_pac_auto` forwards `end_season` to `_manage_predictive_heating`, so the existing 22 °C reserve can restart the PAC. A season-level authorization gate is needed in addition to MPC decisions.
