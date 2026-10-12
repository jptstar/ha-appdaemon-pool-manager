# Hybrid end-of-season heating policy

## Policy (implemented in feature branch)

- Applies only to the **heating** selector `input_select.piscine_chauffage`.
  The separate filtration selector `input_select.mode_fonctionnement_piscine`
  and its **Hors Gel** mode remain unchanged.
- Before **31 October**, the 15-day MPC may prepare achievable bathing
  opportunities. Without a viable opportunity the heat pump is paused,
  including otherwise automatic **PRESERVE at 22 °C**.
- If the outdoor 7-day mean stays below **16 °C for 72 hours** and there is
  still no reachable bathing window, an **early closure is recommended** but
  never automatically made permanent. Turn on the HA closure helper to confirm.
- At **31 October 00:00 local time**, the end-season heating policy closes
  permanently, stores the closure in Home Assistant, and blocks heating
  modes including Turbo and pending start requests.
- A deliberate transition to **Début de saison • Smart** unlocks the new
  season. A warm spell by itself never clears the lock.
- A temporary override button authorizes normal MPC heating for **24 h**
  *before* permanent closure. This is not unconditional heating and never
  bypasses hydraulic flow, compressor limits or the forced stop.
- Missing/stale weather pauses heating reversibly; it never justifies
  early definitive closure.

## AppDaemon settings

Set `chauffage_fin_saison_hybride: true` (false by default for existing users).

```yaml
  chauffage_fin_saison_hybride: true
  chauffage_fin_saison_date: "10-31"
  chauffage_fin_saison_seuil_7j_c: 16
  chauffage_fin_saison_confirmation_h: 72
  chauffage_fin_saison_derogation_h: 24
  entity_chauffage_saison_terminee: input_boolean.piscine_chauffage_saison_terminee
  entity_chauffage_froid_depuis: input_datetime.piscine_chauffage_froid_depuis
  entity_chauffage_derogation_jusqua: input_datetime.piscine_chauffage_derogation_jusqua
  entity_chauffage_derogation_bouton: input_button.piscine_chauffage_derogation_24h
  entity_temperature_exterieure_moyenne_7j: sensor.temperature_exterieure_moyenne_7j
```

Create the helpers described in
`examples/home_assistant_heating_helpers_v060.yaml` first.
Do not repeat a top-level heading (e.g. `input_datetime`) twice in one
Home Assistant package.

## Operator instructions

- Monitor `season_state`, `season_reason`,
  `season_closure_recommended`, `season_closed` on
  `sensor.piscine_chauffage_predictif`.
- For a reversible pause: take no action; a reachable swimming window
  allows heat to resume.
- To confirm early definitive season closure: switch on
  `input_boolean.piscine_chauffage_saison_terminee`.
- To permit MPC heating for a day before closing: press
  `input_button.piscine_chauffage_derogation_24h`.
- To open a new season: deliberately select
  `Début de saison • Smart`.

## Simulation scope

`pytest -q` checks decision boundaries, persistence and restart cases,
forecast outages, no-bathing preservation suppression, delayed PAC starts,
manual overrides and simulated MPC orchestration. These are mocked I/O
simulations, not tests on a physical heat pump. Filtration and frost logic
are intentionally unmodified.
