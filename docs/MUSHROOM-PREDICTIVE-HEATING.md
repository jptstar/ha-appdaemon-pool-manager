# Mushroom — predictive pool heating

Pool Manager can expose the optional virtual entity configured with `entity_chauffage_predictif_status`. The examples below assume `sensor.pool_predictive_heating`.

The v0.8 adaptive planner publishes a **current thermal decision** plus an energy-optimized receding-horizon MPC forecast. The future plan is recalculated continuously rather than executed as a rigid timer:

- `action`: `WAIT`, `PRESERVE`, `PREHEAT` or `MAINTAIN`;
- `certified_water_temperature`: last pool temperature accepted after the configured mixing cycle;
- `certified_water_at`: time of that certified measurement;
- `water_temperature_estimated`: current thermal estimate between certified measurements;
- `measurement_active`: whether the 70% / 15-minute reference-mixing cycle is in progress;
- `next_swim_date`: selected useful bathing day;
- `next_swim_usage_score`: bathing score for the actual usage window;
- `next_swim_weekend`: whether the selected day is Saturday/Sunday;
- `recovery_start_date`: latest date from which recovery normally needs to begin;
- `planner`: `MPC` when the v0.8 optimizer is active;
- `model_confidence`: thermal-learning maturity and sample counts;
- `adaptive_floor_temperature`: current minimum temperature that keeps the optimized recovery feasible;
- `trajectory_target_temperature`: current MPC thermal target;
- `recommended_preset`: Smart/Turbo choice for the current action;
- `night_heating`: true only for an exceptional night recovery;
- `night_required_c`: missing recovery that cannot be covered during daytime;
- `thermal_margin_c`: real current-water margin above the adaptive recoverability floor (negative means recovery is already required);
- `mpc_energy_kwh`: predicted PAC energy for the selected recovery plan;
- `mpc_night_energy_required`: whether the optimizer had to admit exceptional night heating;
- `swim_hour`: today's effective ready-by hour;
- `swim_hour_weekday` / `swim_hour_weekend`: configured weekly policy;
- `mpc_plan`: predicted day-by-day OFF/Smart/Turbo hours, temperatures, energy and `ready_by_hour`;
- `attribute_payload_bytes`, `attribute_payload_compacted` and `attribute_payload_omitted`: Home Assistant payload-budget diagnostics;
- `predicted_night_loss_c`: predicted cooling before the selected bathing day;
- `learned_heating_rates`: learned °C/h, power and kWh/°C by PAC preset and outdoor-temperature range;
- `learned_night_losses`: learned passive night loss by cover state and water/air delta;
- `forecast`: compact weather rows, up to 15 days.

The compact Mushroom card is available in `examples/mushroom_predictive_heating.yaml`.

For a separate compact 15-day Markdown table:

```yaml
type: markdown
title: Piscine · prévision 15 jours
content: |-
  {% set e = 'sensor.pool_predictive_heating' %}
  {% set f = state_attr(e, 'forecast') or [] %}
  {% set selected = state_attr(e, 'next_swim_date') %}
  {% set start = state_attr(e, 'recovery_start_date') %}
  {% set icons = {
    'sunny':'☀️', 'partlycloudy':'🌤️', 'cloudy':'☁️',
    'rainy':'🌧️', 'pouring':'🌧️', 'fog':'🌫️',
    'windy':'💨', 'windy-variant':'💨',
    'lightning':'⛈️', 'lightning-rainy':'⛈️'
  } %}

  | Jour | Météo | Max/Min | Score usage | Plan |
  |:---|:---:|:---:|---:|:---|
  {% for d in f %}
  {% set date = d.get('date') %}
  | **{{ d.get('label','?') }}** {{ '🏊' if d.get('swim',false) else '' }} | {{ icons.get(d.get('condition',''), '🌡️') }} | **{{ d.get('temperature','?') }}°** / {{ d.get('templow','?') }}° | {{ d.get('usage_score',d.get('strategic_score',0))|round(0)|int }} | {% if d.get('mpc_heat_hours',0)|float > 0 %}🔥 {{ d.get('mpc_preset','Smart') }} {{ d.get('mpc_heat_hours') }}h{% elif d.get('swim',false) %}🏊 baignade{% else %}—{% endif %} |
  {% endfor %}
```

`brassage_nuit_intelligent: false` remains compatible: no periodic night mixing is needed. Night-loss learning compares the last certified measurement before the long stop with the first certified measurement after the next real mixing cycle.
