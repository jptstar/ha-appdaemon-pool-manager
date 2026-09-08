# Changelog

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
