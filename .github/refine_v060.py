from pathlib import Path
import re


def replace_once(path, old, new, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match in {path}, got {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def regex_once(path, pattern, replacement, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match in {path}, got {count}")
    p.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# Predictive planner: use weather quality to opportunistically preload a small
# fraction earlier when the day before is substantially worse.
# ---------------------------------------------------------------------------
replace_once(
    "apps/pool_manager/pool_predictive.py",
    "    segments = []\n    remaining = required\n\n    prev_start, prev_end = _window(\n",
    '''    segments = []
    remaining = required

    # Weather-aware preload: the day before remains the primary preparation day,
    # but if it is markedly cold/cloudy/rainy and a recent earlier day is much
    # warmer/sunnier, shift at most 25% (max 2 h) to that better window. The
    # majority still runs the day before, preserving comfort and limiting losses.
    forecast_by_date = {
        item.get("date"): item
        for item in (forecast or [])
        if isinstance(item, dict) and item.get("date") is not None
    }
    previous_quality = float(
        (forecast_by_date.get(previous_day) or {}).get("heating_quality") or 50.0
    )
    if required >= 4.0 and previous_day > now.date():
        candidates = []
        day_cursor = previous_day - datetime.timedelta(days=1)
        for age in range(1, 4):
            if day_cursor < now.date():
                break
            quality = float(
                (forecast_by_date.get(day_cursor) or {}).get("heating_quality") or 0.0
            )
            # Penalize older heat storage so weather must be clearly better.
            effective_quality = quality - (age - 1) * 8.0
            candidates.append((effective_quality, quality, day_cursor))
            day_cursor -= datetime.timedelta(days=1)
        if candidates:
            _, best_quality, best_day = max(candidates, key=lambda item: item[0])
            if best_quality >= previous_quality + 25.0:
                smart_hours = min(2.0, required * 0.25)
                smart_start, smart_end = _window(
                    best_day,
                    previous_day_start,
                    previous_day_end,
                    tzinfo,
                )
                clipped_smart = _clip_window(
                    smart_start,
                    smart_end,
                    now,
                    deadline,
                )
                if clipped_smart is not None:
                    segment, remaining = _allocate_latest(
                        smart_hours,
                        clipped_smart[0],
                        clipped_smart[1],
                        "weather_preheat",
                    )
                    if segment:
                        segments.append(segment)
                        # _allocate_latest only returns the unallocated fraction
                        # of smart_hours, so deduct the amount actually used from
                        # the global remaining heat requirement.
                        remaining = required - float(segment["hours"])

    prev_start, prev_end = _window(
''',
    "weather-aware preload",
)

# ---------------------------------------------------------------------------
# Runtime: season-start naming, compact diagnostic publishing, stale-demand fix.
# ---------------------------------------------------------------------------
replace_once(
    "apps/pool_manager/pool_heating.py",
    '        self.chauffage_premiere_chauffe_marge_c = float(\n'
    '            self.args.get("chauffage_premiere_chauffe_marge_c", 0.3)\n'
    '        )\n',
    '        self.chauffage_premiere_chauffe_marge_c = float(\n'
    '            self.args.get(\n'
    '                "chauffage_debut_saison_marge_c",\n'
    '                self.args.get("chauffage_premiere_chauffe_marge_c", 0.3),\n'
    '            )\n'
    '        )\n',
    "season-start margin alias",
)

replace_once(
    "apps/pool_manager/pool_heating.py",
    '                if not self.gestion_pac_auto:\n'
    '                    self._update_predictive_diagnostics(\n'
    '                        kind, override="⏸ Auto PAC non piloté"\n'
    '                    )\n'
    '                    return\n',
    '                if not self.gestion_pac_auto:\n'
    '                    self.chauffage_predictif_heat_requested = False\n'
    '                    self._cancel_chauffage_start()\n'
    '                    self._update_predictive_diagnostics(\n'
    '                        kind, override="⏸ Auto PAC non piloté"\n'
    '                    )\n'
    '                    return\n',
    "clear stale auto demand",
)

replace_once(
    "apps/pool_manager/pool_predictive_runtime.py",
    "        self.chauffage_predictif_last_log_signature = None\n"
    "        self.chauffage_predictif_last_rate = self.chauffage_predictif_gain_chauffe_c_par_h\n",
    "        self.chauffage_predictif_last_log_signature = None\n"
    "        self.chauffage_predictif_last_status_signature = None\n"
    "        self.chauffage_predictif_last_rate = self.chauffage_predictif_gain_chauffe_c_par_h\n",
    "status signature initialization",
)

replace_once(
    "apps/pool_manager/pool_predictive_runtime.py",
    '            "water_temperature": water,\n'
    '            "target_temperature": target,\n',
    '            "water_temperature": (\n'
    '                round(float(water), 1) if water is not None else None\n'
    '            ),\n'
    '            "target_temperature": (\n'
    '                round(float(target), 1) if target is not None else None\n'
    '            ),\n',
    "round diagnostic temperatures",
)

replace_once(
    "apps/pool_manager/pool_predictive_runtime.py",
    '''        try:
            self.set_state(
                self.entity_chauffage_predictif_status,
                state=self._predictive_status_state(plan, kind, override=override),
                attributes=attributes,
            )
            self._recover("chauffage_predictif_status")
        except Exception as exc:
''',
    '''        state = self._predictive_status_state(plan, kind, override=override)
        signature = (
            state,
            attributes.get("water_temperature"),
            attributes.get("target_temperature"),
            attributes.get("heating_now"),
            attributes.get("next_swim_date"),
            attributes.get("next_swim_score"),
            attributes.get("next_heating_start"),
            attributes.get("next_heating_end"),
            attributes.get("reason"),
            tuple(
                (slot.get("start"), slot.get("end"), slot.get("kind"))
                for slot in schedule
            ),
            attributes.get("forecast_updated_at"),
        )
        if signature == self.chauffage_predictif_last_status_signature:
            return

        try:
            self.set_state(
                self.entity_chauffage_predictif_status,
                state=state,
                attributes=attributes,
            )
            self.chauffage_predictif_last_status_signature = signature
            self._recover("chauffage_predictif_status")
        except Exception as exc:
''',
    "deduplicate predictive status",
)

# ---------------------------------------------------------------------------
# Canonical AppDaemon example.
# ---------------------------------------------------------------------------
regex_once(
    "examples/filtration_piscine.yaml",
    r"  # v0\.4\.4: one Home Assistant selector owns all user-facing heating policy\..*?"
    r"  # Legacy v0\.2\.x-v0\.4\.3 compatibility only\.",
    '''  # v0.6.0: one selector controls the heating profile. Recommended options:
  # Désactivé / Automatique / Début de saison • Smart / Fin de saison • Smart /
  # Turbo • 1 h / 2 h / 3 h / 6 h / 12 h / 1 jour / 2 jours / 3 jours.
  # The old `Première chauffe • Smart` wording is still accepted for migration.
  entity_chauffage: input_select.pool_heating

  entity_chauffage_timer: timer.pool_heating
  entity_chauffage_precedent: input_text.pool_heating_previous

  chauffage_preset_auto: Smart
  chauffage_preset_smart: Smart
  chauffage_preset_turbo: Turbo
  chauffage_debut_saison_marge_c: 0.3

  # v0.6.0 season-wide predictive heating. When enabled it is used in both
  # Automatique and Fin de saison. Début de saison heats to setpoint and then
  # automatically switches the selector to Automatique.
  chauffage_predictif: false
  entity_meteo_chauffage_predictif: weather.home

  # Optional virtual sensor published by AppDaemon for Mushroom/dashboard use.
  # No Home Assistant helper is required; choose a unique sensor entity id.
  entity_chauffage_predictif_status: sensor.pool_predictive_heating

  chauffage_predictif_horizon_jours: 10
  chauffage_predictif_prevision_refresh_s: 1800
  chauffage_predictif_prevision_max_age_s: 21600

  # Bathing opportunity score: temperature + sun/clouds - rain - wind.
  chauffage_predictif_temperature_baignade_min_c: 21
  chauffage_predictif_temperature_baignade_ideale_c: 26
  chauffage_predictif_score_baignade_min: 55
  chauffage_predictif_heure_baignade: "16:00:00"

  # The water should normally be ready before late morning. Heating is planned
  # first on the previous day; target-day morning is only a top-up window.
  chauffage_predictif_heure_eau_prete: "11:00:00"
  chauffage_predictif_veille_debut: "12:00:00"
  chauffage_predictif_veille_fin: "20:00:00"
  chauffage_predictif_matin_debut: "07:00:00"

  # Initial PAC model. Runtime learning refines °C/h in outdoor-temperature bins.
  chauffage_predictif_gain_chauffe_c_par_h: 0.30
  chauffage_predictif_apprentissage: true
  chauffage_predictif_apprentissage_min_s: 1800
  chauffage_predictif_apprentissage_alpha: 0.25

  # Automatic keeps more thermal reserve during a long bad-weather spell;
  # End of season allows a larger drift before preserving recoverability.
  chauffage_predictif_plancher_auto_delta_c: 2.0
  chauffage_predictif_plancher_fin_saison_delta_c: 4.0
  chauffage_predictif_recharge_plancher_c: 0.5

  chauffage_predictif_marge_arret_c: 0.2
  chauffage_predictif_marge_planification_h: 0.5
  chauffage_predictif_marge_derniere_occasion_h: 1.0

  # v0.5 fin_saison_* keys are accepted as migration aliases. If an existing
  # configuration has `fin_saison_predictif: true`, v0.6 upgrades it to the
  # common season-wide engine.

  # Legacy v0.2.x-v0.4.3 compatibility only.''',
    "replace canonical predictive example",
)

# ---------------------------------------------------------------------------
# README: architecture and v0.6 behavior.
# ---------------------------------------------------------------------------
replace_once(
    "README.md",
    "    pool_end_season.py      # predictive 7-10 day end-of-season planner\n",
    "    pool_predictive.py      # pure 7-10 day predictive planner\n"
    "    pool_predictive_runtime.py # weather cache, PAC learning + HA diagnostics\n",
    "README architecture",
)

regex_once(
    "README.md",
    r"## Predictive end-of-season heating\n.*?\n## Heating override boundary",
    '''## Season-wide predictive heating

Since v0.6.0 the predictive planner is no longer limited to `Fin de saison • Smart`. With `chauffage_predictif: true`, the same weather-aware engine runs in **Automatique** and **Fin de saison**, while the modes only change the thermal-reserve profile.

```yaml
chauffage_predictif: true
entity_meteo_chauffage_predictif: weather.home
entity_chauffage_predictif_status: sensor.pool_predictive_heating
chauffage_predictif_horizon_jours: 10
chauffage_predictif_heure_baignade: "16:00:00"
chauffage_predictif_heure_eau_prete: "11:00:00"
chauffage_predictif_gain_chauffe_c_par_h: 0.30
chauffage_predictif_plancher_auto_delta_c: 2.0
chauffage_predictif_plancher_fin_saison_delta_c: 4.0
```

The engine asks Home Assistant for daily forecasts with `weather.get_forecasts`, scores each day from forecast high temperature, sun/cloud condition, rain probability/amount and wind, and marks the next credible bathing opportunity. A good day followed by several poor days is treated as a likely last opportunity and receives additional scheduling margin.

Heating is planned **before** the swimming day. By default the water is targeted to be ready at 11:00, while the main heating window is the previous day from 12:00 to 20:00. A small recovery therefore happens the previous afternoon/evening rather than waiting until the swimming morning. If more hours are needed, the target-day morning is used as a top-up; if even that is insufficient, the planner uses earlier windows and can enter immediate guarantee/catch-up mode.

The planner also computes a daytime heating-quality score from outdoor temperature and sun/cloud conditions. If the day before is substantially worse than a recent warmer/sunnier day and the recovery is large enough, a limited weather-aware preload may be shifted earlier while most heat remains scheduled for the day before. This preserves comfort while improving the chance of good PAC COP and available PV.

During a long bad-weather spell, **Automatique does not maintain the full setpoint continuously**. It allows the pool to drift to a recovery reserve (2 °C below setpoint by default), while `Fin de saison` allows a larger drift (4 °C by default). When a credible bathing day appears, the planner recalculates the required recovery and builds a new schedule automatically.

`Début de saison • Smart` replaces the old user-facing `Première chauffe • Smart` wording. It deliberately heats to the PAC setpoint, then automatically switches the selector to `Automatique`. The old selector wording is still recognized so existing Home Assistant configurations do not fail during migration. Turbo remains an explicit immediate override; `Désactivé`, `Hors Gel` and forced stop stay above the predictive planner.

The configured °C/h value is only the initial PAC model. While the PAC is really heating with confirmed circulation, Pool Manager learns observed water-heating speed in broad outdoor-temperature bins and progressively blends those measurements into future scheduling. Implausible samples are rejected. The learning is intentionally conservative and falls back to the configured rate after restart.

If `entity_chauffage_predictif_status` is configured, AppDaemon publishes a virtual sensor intended for dashboards. Its attributes include the 7-10 day forecast rows, bathing score, `swim` marker, heating slots, next heating start/end, ready-by time, dynamic floor, estimated heating rate and learned rate buckets. This makes a Mushroom card able to show `🏊` for the selected bathing day and `🔥`/`⏸` for planned heating.

The v0.5 `fin_saison_*` configuration names remain migration aliases. In particular, an existing `fin_saison_predictif: true` enables the common v0.6 predictive engine. AppDaemon 4.5+ is recommended because Home Assistant service response data is required for `weather.get_forecasts`.

## Heating override boundary''',
    "README predictive section",
)

# ---------------------------------------------------------------------------
# Changelog.
# ---------------------------------------------------------------------------
replace_once(
    "CHANGELOG.md",
    "# Changelog\n\n",
    '''# Changelog

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

''',
    "CHANGELOG v0.6",
)

# ---------------------------------------------------------------------------
# Additional regression test for weather-aware preload.
# ---------------------------------------------------------------------------
test_path = Path("tests/test_predictive_heating.py")
test_text = test_path.read_text(encoding="utf-8")
if "test_weather_quality_can_shift_limited_preheat_earlier" not in test_text:
    test_text += '''\n\ndef test_weather_quality_can_shift_limited_preheat_earlier():
    now = datetime.datetime(2026, 9, 13, 10, 0)
    forecast = _forecast(
        now,
        [
            (20, "cloudy", 40, 8),
            (27, "sunny", 0, 5),
            (15, "rainy", 90, 20),
            (27, "sunny", 0, 5),
        ],
    )
    ready = datetime.datetime(2026, 9, 16, 11, 0)
    swim = datetime.datetime(2026, 9, 16, 16, 0)
    slots = module.build_heating_schedule(
        now=now,
        ready_datetime=ready,
        swim_datetime=swim,
        required_hours=8.0,
        forecast=forecast,
    )
    assert any(slot["kind"] == "weather_preheat" for slot in slots)
    assert any(slot["kind"] == "previous_day" for slot in slots)
    smart = next(slot for slot in slots if slot["kind"] == "weather_preheat")
    assert smart["hours"] <= 2.0
'''
    test_path.write_text(test_text, encoding="utf-8")

print("v0.6.0 refinements applied")
