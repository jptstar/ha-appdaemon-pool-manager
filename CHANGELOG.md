# Changelog

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
- Preserve previous behavior when the override entity is not configured.
- Add regression tests for override detection and PAC-demand fallback.

## 0.1.3 - Simpler status fields

- Reuse the existing short and detailed Home Assistant status helpers for quota information.
- Remove the need for the extra `message_filtration_quota` helper introduced in 0.1.2.
- Keep the short status focused on the current Pool Manager decision.
- Prefix the existing detailed status with `Besoin / Effectué / Restant` in hours and minutes.
- Remove duplicate legacy `x.y/z.yh` progress fragments from the detailed status.
- Add regression tests for merged quota/detail formatting.

## 0.1.2 - Structured quota status

- Add an optional structured Home Assistant quota summary with need, completed equivalent filtration, remaining equivalent filtration and the current Pool Manager decision.
- Format quota durations as hours and minutes for easier dashboard reading.
- Keep the existing short status and detailed status outputs unchanged.
- Keep the new quota output fully optional through `message_filtration_quota`.
- Add regression tests for quota formatting and non-negative remaining time.

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
