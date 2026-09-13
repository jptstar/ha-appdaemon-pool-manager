# Mushroom — predictive pool heating

Pool Manager can expose the optional virtual entity configured with `entity_chauffage_predictif_status`. The example below assumes `sensor.pool_predictive_heating`.

The state gives the current high-level decision. Attributes expose the detailed plan. In the forecast rows:

- `🏊` means the day selected as the next likely bathing opportunity;
- `🔥` means a heating slot is planned that day;
- `⏸` means no heating slot is planned that day.

The card example is available in `examples/mushroom_predictive_heating.yaml`.
