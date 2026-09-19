# Changelog

## 0.7.3 - Exact certified-measurement pump speed

- Lock the filtration pump to the configured certified-measurement reference speed exactly (70% by default) while predictive temperature stabilization is active.
- Prevent solar, quota, catch-up, or other normal strategy requests from raising the pump above the measurement reference speed during the short certification cycle.
- Bypass normal speed ramp/delay rules for this explicit reference cycle so a pump already at 100% drops directly to 70% instead of stepping down slowly.
- Avoid redundant fan commands when the pump is already at the configured measurement speed.
- Keep the 47% hydraulic minimum unchanged outside certified measurement cycles.
- Rename the user-facing measurement state to `🌀 Stabilisation mesure température`.


## 0.7.2 - Predictive WAIT safety and diagnostic cleanup

- Stop the PAC immediately when the adaptive planner returns `WAIT` or another no-heat decision; thermal learning and certification may keep pump circulation only, never unnecessary heat production.
- Convert an in-progress `heating_learning` / `target_check` measurement into pump-only temperature stabilization after the PAC is stopped.
- Keep the configured 70% / 15-minute certified-measurement cycle available without using PAC heat as a prerequisite.
- Replace the predictive virtual sensor attribute set atomically so obsolete v0.6.x diagnostics such as `schedule`, `heating_slots`, `ready_by` and `swim_datetime` disappear after update.
- Rename the measurement status to `🌀 Stabil. mesure température` for a clearer dashboard message.


## 0.7.1 - Certified adaptive thermal decisions

- Replace the remaining day-capacity scheduler semantics with a decisive daily action model: `WAIT`, `PRESERVE`, `PREHEAT` or `MAINTAIN`.
- Add a daily thermal trajectory target so future good weather does not force an immediate return to the full water setpoint; the controller heats only as much as is needed today to keep the selected bathing day recoverable.
- Add certified pool-water measurements: 47% remains the hydraulic low limit, but the thermal model only certifies water after 15 minutes at 70% by default plus a short stability check.
- Prevent `mem_temp` and low-flow/stale pipe readings from entering thermal learning.
- Learn passive overnight loss only between two certified pool measurements; reject samples contaminated by PAC activity or a cover-state change.
- Learn PAC Smart/Turbo gain from certified measurements and retain average electrical power / derived kWh per °C when available.
- Re-certify periodically during long PAC runs so the learned gain model can continue improving automatically.
- Use the last certified temperature plus the learned model as an estimate while the pump is stopped, then correct it on the next certified mixing cycle.
- Add optional near-term hourly-weather refinement. Weekdays prioritize approximately 16:00-20:00; weekends receive additional bathing priority and a broader usage window.
- Prefer a better nearby bathing opportunity over the first marginal weather day, while still avoiding excessive delay.
- Reject weather-friendly days that are physically unreachable even with exceptional Turbo/day + Turbo/night recovery.
- Prefer Smart when it can preserve the trajectory; select Turbo only when Smart has insufficient remaining capacity.
- Allow night recovery only when daytime capacity cannot restore the trajectory, and stop again once the trajectory target is reached rather than running through the whole night.
- Keep long bad-weather periods PAC-off above the recoverability floor in both Automatique and Fin de saison • Smart.
- Separate the real current cover state used for learning from the expected future night-cover state used for planning; future nights default to `closed` when a cover entity is configured.
- Expose certified temperature, measurement state, daily action, trajectory target, thermal margin, usage-window score and night-recovery need through the predictive status sensor.


## 0.7.0 - Self-learning thermal recovery planner

- Replace the fixed bathing-hour / heating-slot scheduler with a day-level thermal recovery planner.
- Keep the 15-day weather outlook, but only act when the learned pool/PAC behavior says recovery must really begin.
- Learn real PAC water-heating gain in °C/h separately by outdoor-temperature range and PAC preset (Smart, Turbo, etc.).
- Learn passive night cooling in °C/h from water/air temperature difference and pool-cover state.
- Persist both learned models outside the HACS-managed package so AppDaemon restarts and HACS upgrades keep the thermal history.
- Skip a weather-friendly day when the pool is no longer thermally recoverable in time instead of marking it as a bathing day anyway.
- Calculate the actual recovery start day instead of using fixed J-2/J-5 rules.
- Prefer Smart daytime heating, then Turbo daytime, then allow night heating only when daytime capacity is insufficient.
- Apply the same bad-weather stop/restart strategy in Automatique and Fin de saison Smart.
- Add an optional absolute minimum-water-temperature floor to keep the pool recoverable through long cold spells.
- Keep `brassage_nuit_intelligent: false` independent from genuine heating demand: periodic night mixing remains disabled while exceptional pump+PAC night recovery can still be authorized.
- Remove the synthetic 16:00 bathing deadline, ready-by time, previous-day/morning/emergency slots, operational-horizon gate, last-chance margin logic and predictive slot anti-chatter layer.
- Remove the obsolete duplicate `pool_end_season.py` planner and its dedicated tests.
- Replace old slot diagnostics with recovery start date, recommended PAC preset, night-heating requirement, predicted night loss, projected no-heat water temperature and learned-model diagnostics.


## 0.6.1 - 15-day strategic weather outlook

- Extend predictive weather ingestion from 10 to 15 days when the configured Home Assistant provider supplies that range.
- Add distance-confidence weighting: J0-J3 strong, J4-J7 medium, J8-J10 trend, J11-J15 indicative.
- Keep the raw bathing score while exposing a new `strategic_score`, `horizon_weight` and `confidence` tier in forecast diagnostics.
- Add a 3-day operational planning horizon by default so distant forecasts can inform strategy without triggering large immediate heating expenditure.
- Continue reserve-floor maintenance while a distant opportunity is only being observed; full comfort recovery is planned once it enters the operational horizon.
- Expose forecast/operational horizon values and next-swim confidence in the predictive Home Assistant sensor.

## 0.6.0 - Season-wide predictive heating

- Promote predictive heating from a Fin-de-saison-only feature to one common weather-aware engine used by `Automatique` and `Fin de saison`.
- Rename the recommended `Première chauffe • Smart` selector option to `Début de saison • Smart`; keep the old wording accepted as a migration alias and automatically switch to `Automatique` after the setpoint is reached.
- Score bathing opportunities across up to 10 forecast days from temperature, sun/cloud conditions, precipitation and wind, with last-opportunity detection after a good day followed by a poor spell.
- Plan heating primarily on the **previous day** and target water ready by 11:00 by default; use the swimming-day morning only as a top-up and enter immediate guarantee mode if preferred windows become insufficient.
- Add weather-aware limited preload when an earlier day is substantially warmer/sunnier than a poor day-before window, while keeping most preparation close to the bathing day.
- Let `Automatique` drift to a configurable recovery reserve during long bad-weather periods instead of maintaining full setpoint; allow a larger reserve delta in `Fin de saison`.
- Learn actual PAC heating speed from real heating cycles in broad outdoor-temperature bins and blend learned performance into future scheduling.
- Prefer the persisted thermal-reference temperature while the pump is stopped instead of blindly trusting an available but potentially stale pipe sensor.
- Publish an optional virtual predictive-heating sensor with forecast rows, `🏊` bathing marker, `🔥` heating slots, ready-by time, next start/end, dynamic floor and learned PAC-rate diagnostics for Mushroom dashboards.
- Deduplicate diagnostic sensor updates to avoid second-by-second recorder churn.
- Keep pump-first PAC sequencing, post-circulation, forced stop, Hors Gel and PAC-flow fail-safe safety above every predictive decision.
- Accept v0.5 `fin_saison_*` keys as migration aliases; an existing `fin_saison_predictif: true` upgrades to the common v0.6 engine.

## 0.5.0 - Predictive end-of-season heating

- Add an opt-in predictive strategy for `Fin de saison • Smart` so the pump/PAC no longer need to remain available 24/7 merely to preserve the setpoint.
- Read Home Assistant daily weather forecasts with `weather.get_forecasts` and use up to a 10-day strategic horizon, limited by the number of days the configured provider actually supplies.
- Score likely bathing opportunities from forecast high temperature, sun/cloud condition, precipitation probability/amount and wind.
- Detect a good day followed by several poor days as a likely last bathing opportunity and give it additional scheduling margin.
- Work backwards from a configurable bathing time and measured/estimated PAC heating rate, placing required heating hours as late as possible in a preferred daytime window.
- Avoid needless overnight heating when a warm/sunny next day still provides enough time to recover the requested water temperature; start earlier, including overnight, only when required to meet the bathing deadline.
- When no credible bathing window is visible, maintain only a configurable recovery floor instead of the full PAC setpoint.
- Cache forecasts, tolerate a temporary refresh failure with a bounded stale cache, and fall back to recovery-floor behavior when forecast response data is unavailable.
- Keep legacy 24/7 `Fin de saison` behavior unchanged unless `fin_saison_predictif: true` is explicitly configured.
- Keep forced stop, Hors Gel and PAC minimum-flow fail-safes above the predictive planner.

## 0.4.6 - Mode-aware local pump control

- Revert the v0.4.5 last-command pump-speed ownership experiment after production feedback showed unexpected speed behavior, including an observed 100% / 2900 rpm condition.
- Restore normal automatic speed regulation against the physical `fan.percentage` reported by Home Assistant.
- Add optional pump-integration local-panel entities. `Température` and `Marche Forcée` enable local-control assist, while `Intelligent`, `Hors Gel` and `Arrêt Forcé` disable it so automatic and safety control remain authoritative.
- In `Température`, send the configured speed once at mode/start initialization instead of rewriting it on every periodic evaluation; after the pump integration quiet timer expires, a user may change speed locally without Pool Manager immediately overwriting it.
- Keep `Marche Forcée` non-intrusive after startup: it keeps the running speed and allows the local panel to take over when available.
- Keep startup, PAC minimum-flow, freeze protection and explicit safety commands authoritative.
- The local-panel integration entities are optional; installations without them keep previous generic fan behavior.

## 0.4.5 - Manual pump speed handoff

- Send normal pump-speed commands only when Pool Manager's requested target changes by at least the configured delta, instead of repeatedly rewriting the currently expected speed.
- Compare normal targets with the last speed command emitted by Pool Manager, so an unchanged automatic target does not immediately overwrite a manual speed adjustment made through Home Assistant or the pump integration.
- When the automatic target really changes, resume control from the physical `fan.percentage` reported by Home Assistant and keep the existing ramp and minimum-delay limits.
- Stop forcing repeated speed writes from the normal `Température` filtration path during periodic re-evaluation.
- Keep forced speed commands for delayed pump startup and safety paths such as PAC minimum flow and freeze protection.
- No YAML migration is required.
- Add regression tests for manual handoff, automatic takeover on target change and forced safety commands.

## 0.4.4 - Unified heating modes

- Add optional `entity_chauffage` support for one Home Assistant selector that centralizes all user-facing pool-heating policy.
- Add `Désactivé`, `Automatique`, `Première chauffe • Smart`, `Fin de saison • Smart` and timed Turbo strategies.
- Keep `Automatique` on the existing seasonal weather/window policy and continue respecting the optional `entity_mode_auto` gate.
- Make `Première chauffe • Smart` establish pump flow first, run the PAC in `Heat/Smart`, then return the selector to `Automatique` when the measured water reaches the climate setpoint within the configured margin.
- Make `Fin de saison • Smart` deliberately bypass the normal seasonal weather and 08:00-20:00 gate, keep `Heat/Smart` available 24/7 and maintain the minimum PAC circulation so the heat pump can preserve the requested water temperature during cold nights.
- Add timed `Turbo` modes from 1 hour to 3 days. An optional Home Assistant timer provides persistence and an optional input-text helper stores the previous heating mode; without the HA timer AppDaemon falls back to an in-memory timer.
- Restore the previous non-Turbo heating mode automatically when a Turbo timer finishes.
- Keep forced stop, `Hors Gel` and PAC-flow fail-safe safety above every heating strategy.
- Keep the legacy `entity_derogation_chauffage` path backward-compatible when the new selector is not configured.
- Add regression tests and update the canonical AppDaemon example.

## 0.4.3 - PAC automatic mode gate

- Add optional `entity_mode_auto` support for Home Assistant's master pool automatic-mode boolean.
- When configured, suspend normal automatic PAC start/stop policy unless the entity is `on`.
- Abort a pending automatic PAC start immediately if automatic mode is disabled during the pump-first startup sequence.
- Preserve the current PAC state when automatic mode is disabled, matching the previous Home Assistant automation behavior instead of forcing an automatic shutdown.
- Keep temporary heating override handling and PAC flow fail-safe protection independent of the automatic-mode gate.
- Preserve backward compatibility when `entity_mode_auto` is omitted.
- Add regression tests and canonical YAML examples for the new gate.

## 0.4.2 - Compact status history

- Stabilize `Message Filtration Piscine détail` so second-by-second timer countdowns no longer create Home Assistant state-history spam.
- Replace changing countdown values with semantic states such as `temporisation arrêt`, `temporisation démarrage`, `attente surplus stable` and `anti-coupure`.
- Remove instantaneous surplus, grid, PV and pump watt values from the detailed status because those values already belong in the dedicated debug field.
- Preserve useful operational context such as daily quota, speed, PAC state, quota decisions, deadlines and freeze-protection information.
- Deduplicate repeated semantic timer fragments.
- Keep filtration, PAC, solar, quota and fail-safe control logic unchanged.
- Add regression tests proving that changing timer seconds and electrical values no longer churn the detailed status.

## 0.4.1 - Cloudy-day catch-up fix

- Allow Intelligent-mode daytime sanitary catch-up to start even when PV surplus is below the normal 500 W solar-start threshold.
- Keep the PV threshold and stability delay mandatory for opportunistic solar-only starts.
- Preserve the daylight gate: cloudy-day catch-up still cannot start before sunrise.
- Add regression tests for zero-surplus daytime catch-up and the pre-sunrise block.

## 0.4.0 - Daylight-aware Intelligent mode

- Use AppDaemon's real sunrise/sunset information for the main Intelligent filtration window when available; retain the configured fixed solar hours as an automatic fallback.
- Pace expected daily filtration progress across the real daylight period instead of a permanently fixed 09:00-18:00 window.
- Prevent a large electrical export from starting Intelligent filtration before sunrise when real-sun tracking is enabled.
- Start evening catch-up after the real sunset while keeping the existing configured catch-up deadline.
- Turn the existing `mem_temp` helper into a smoothed thermal reference: while the pump is stopped, the next filtration target starts from the representative stored water temperature.
- Once circulation has run long enough for the water probe to be representative, the live filtration target immediately uses the real measured temperature and the persisted thermal reference is updated progressively.
- Never update the persisted thermal reference from a fail-safe substituted water temperature; only a valid physical water probe may refresh it.
- Disable periodic night mixing by default in `Intelligent` mode because it is no longer required merely to refresh temperature.
- Keep `Hors Gel` periodic and continuous circulation completely independent and unchanged by the Intelligent night-mixing setting.
- Add regression tests for thermal-reference smoothing, daylight quota pacing and the separation between Intelligent night mixing and freeze protection.

## 0.3.1 - Cold-water chlorination protection

- Inhibit chlorinator production when the real pool-water temperature is at or below 15 °C by default.
- Add 1 °C hysteresis by default: chlorination is only released again at or above 16 °C, avoiding rapid on/off switching around the threshold.
- Treat an `unknown` or `unavailable` physical water-temperature entity as unsafe for electrolysis and immediately command zero production.
- Keep the existing filtration temperature fail-safe separate: filtration may use memory/last-valid/conservative fallback, while chlorination deliberately requires a valid physical water-temperature reading.
- Re-evaluate chlorination immediately on every water-temperature state change, including transitions to `unavailable`.
- Keep the protection enabled by default with optional `protection_electrolyse_froid`, `electrolyse_temperature_arret_c` and `electrolyse_temperature_reprise_c` settings.
- No Home Assistant automation change is required for this protection; it uses the existing `temperature_eau` and chlorinator entities.

## 0.3.0 - PAC automation and fail-safe safety layer

- Add an opt-in automatic PAC policy using the existing warm/cold thresholds and the 08:00-20:00 operating window.
- Sequence every automatic PAC start as pump ON -> confirmed minimum circulation speed -> PAC `Heat`; abort the PAC start if circulation cannot be confirmed within the configured timeout.
- Sequence normal PAC shutdown as PAC `Off` -> configurable post-circulation -> return to the normal filtration strategy.
- Keep the existing Home Assistant temporary heating override as a separate policy boundary; automatic PAC management does not fight an active override.
- Add PAC flow fail-safe: when PAC power indicates activity but pump circulation at the minimum speed is not confirmed, attempt to restore circulation and stop the PAC after a bounded safety timeout when possible.
- Add adaptive freeze protection with hysteresis: continuous minimum circulation at/below the low threshold, release only above the high threshold, while retaining the existing periodic `Hors Gel` circulation outside the critical range.
- Make low outdoor temperature protection independent of the mode-selection automation, so a missed `Hors Gel` mode switch cannot leave the hydraulic circuit static during a real freeze.
- Treat loss of the outdoor-temperature entity while already in `Hors Gel` as a freeze risk and fall back to continuous circulation.
- Keep explicit `Arrêt Forcé` as the highest-priority user command.
- Prevent an unavailable water-temperature sensor from silently becoming 10 °C: use the HA memory helper, then the last valid reading, then a configurable conservative fallback.
- When PAC climate state is unavailable but power proves that the PAC is active, keep a PAC circulation demand instead of assuming the PAC is off.
- Add fail-safe logging, regression tests and canonical configuration examples for the new safety settings.
- Keep PAC automatic takeover and adaptive freeze circulation disabled by default for backwards compatibility; the general fail-safe layer remains enabled by default.

## 0.2.2 - Adaptive filtration target

- Replace the former hot-water polynomial with a continuous adaptive curve: temperature/2 up to 25 °C, then a mild exponential branch above 25 °C.
- Keep the adaptive curve anchored at 12.5 equivalent hours at 25 °C with a 0.05 exponential growth coefficient and the existing 24 h safety cap.
- Keep the former polynomial in the code as a reference helper, but no longer use it for the active `mode_calcul` abaque path.
- Count equivalent filtration from the ratio of estimated hydraulic flow to estimated reference flow instead of the raw pump speed percentage.
- Keep the daily filtered-volume helper explicitly approximate; Pool Manager does not claim to have a physical flow-meter.
- Preserve 70% as the reference filtration speed in the current example configuration.
- Add regression tests for the cool-water T/2 branch, continuity at 25 °C, hot-water exponential values and relative-flow equivalent-hour accounting.

## 0.2.1 - Energy-aware PAC/quota arbitration

- Prevent an impossible daily quota from forcing the pump to 100% indefinitely.
- Distinguish a recoverable critical quota from a quota that is already mathematically impossible before the configured deadline.
- Keep PAC circulation at its configured minimum when grid import exceeds the daytime PAC import allowance and quota is not recoverably critical.
- Hand control back to the existing solar/PID logic while the installation is exporting or remains inside the permitted PAC import margin.
- Preserve the PAC minimum-flow requirement during a heating override.
- Surface `PAC éco réseau`, `quota différé`, `quota critique` and `quota impossible` decisions in the existing status fields.
- Add regression tests based on the production case that previously drove the pump to 100% while importing about 2.7 kW.

## 0.2.0 - Heating override boundary

- Add optional `entity_derogation_chauffage` Home Assistant input.
- Treat an active heating override as a PAC circulation demand in Intelligent mode.
- Re-evaluate Pool Manager immediately when the override state changes.
- Keep Home Assistant responsible for the override timer and PAC mode/preset.
- Reuse the existing PAC priority, quota and solar arbitration instead of adding a second pump-control path.
- Preserve previous behavior when no override entity is configured.
- Add regression tests for override detection and PAC-demand fallback.

## 0.1.3 - Simpler status fields

- Reuse the existing short and detailed Home Assistant status helpers for quota information.
- Remove the need for the extra `message_filtration_quota` helper introduced in 0.1.2.
- Keep the short status focused on the current Pool Manager decision.
- Prefix the existing detailed status with the daily equivalent-filtration state and then keep the useful operating details.
- Remove duplicate legacy `x.y/z.yh` progress fragments from the detailed line.

## 0.1.2 - Structured quota status

- Add an optional structured Home Assistant quota summary with need, completed equivalent filtration, remaining equivalent filtration and the current Pool Manager decision.
- Format quota durations as hours and minutes for easier dashboard reading.
- Keep the existing short status and detailed status outputs unchanged.
- Keep the new quota output fully optional through `message_filtration_quota`.

## 0.1.1 - Chlorinator safety timeout

- Avoid sending a chlorinator production command when the Home Assistant number entity already has the requested value.
- Keep forced-stop safety order: request chlorinator shutdown before stopping the pump.
- Bound the forced-stop chlorinator service wait to 3 seconds by default so an unresponsive chlorinator cannot delay pump shutdown indefinitely.
- Add regression tests for duplicate chlorinator writes, forced-stop timeout handling and pump shutdown after a chlorinator timeout.
- Document `timeout_electrolyseur_force_s` in the example configuration.

## 0.1.0 - Initial public baseline

- Import the existing production AppDaemon pool controller.
- Split the controller into maintainable modules without intentionally changing the production strategy.
- Cap the daily equivalent filtration target to 24 hours everywhere (display, quota and schedule).
- Keep the requested filtration window inside the current day while preserving its duration when possible.
- Make AppDaemon reload/startup non-destructive: no unconditional pump shutdown on `initialize()`.
- Resume the current pump percentage when available.
- Keep Aquagem and AstralPool device handling outside Pool Manager through Home Assistant entities.
- Add HACS AppDaemon repository metadata, example configuration, GPL-3.0 licensing, CI and validation tests.
