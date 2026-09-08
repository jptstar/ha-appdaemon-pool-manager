# Changelog

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
