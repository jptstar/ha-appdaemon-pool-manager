# Mushroom — predictive pool heating

Pool Manager can expose the optional virtual entity configured with `entity_chauffage_predictif_status`. The example below assumes `sensor.pool_predictive_heating`.

The v0.7 planner no longer publishes fixed heating slots such as “07:00–11:00”. It publishes the thermal decision instead:

- `next_swim_date`: next weather day considered worth preparing for;
- `recovery_start_date`: first day on which recovery really needs to start;
- `recommended_preset`: PAC preset selected by the learned thermal model;
- `night_heating_allowed`: true only when daytime heating is insufficient;
- `required_gain_c`: total recovery still required, including predicted losses;
- `predicted_night_loss_c`: estimated cooling before the selected bathing day;
- `projected_without_heat_c`: estimated water temperature if the PAC stays off;
- `learned_heating_rates`: learned °C/h by PAC preset and outdoor-temperature range;
- `learned_night_losses`: learned passive loss by cover state and water/air delta;
- `forecast`: compact weather rows, up to 15 days.

In each `forecast` row:

- `swim: true` marks the selected bathing day;
- `preheat: true` marks a day that belongs to the calculated recovery period;
- `heating: true` means the day is part of recovery or is the bathing day;
- `night_allowed: true` means the calculated recovery cannot be guaranteed with daytime heating alone.

The compact Mushroom card is available in `examples/mushroom_predictive_heating.yaml`.

For a separate compact 15-day Markdown table, this template is intentionally defensive about missing attributes:

```yaml
type: markdown
title: Piscine · prévision 15 jours
content: |-
  {% set f = state_attr('sensor.pool_predictive_heating', 'forecast') or [] %}
  {% set icons = {
    'sunny':'☀️', 'partlycloudy':'🌤️', 'cloudy':'☁️',
    'rainy':'🌧️', 'pouring':'🌧️', 'fog':'🌫️',
    'windy':'💨', 'windy-variant':'💨',
    'lightning':'⛈️', 'lightning-rainy':'⛈️'
  } %}

  | Jour | Météo | Max/Min | Score | Plan |
  |:---|:---:|:---:|---:|:---|
  {% for d in f %}
  | **{{ d.get('label','?') }}** {{ '🏊' if d.get('swim',false) else '' }} | {{ icons.get(d.get('condition',''), '🌡️') }} | **{{ d.get('temperature','?') }}°** / {{ d.get('templow','?') }}° | {{ d.get('strategic_score',0)|round(0)|int }} | {% if d.get('preheat',false) %}🔥 {{ d.get('preset','Smart') }}{% if d.get('night_allowed',false) %} 🌙{% endif %}{% elif d.get('swim',false) %}🌡️ maintien{% else %}—{% endif %} |
  {% endfor %}
```
