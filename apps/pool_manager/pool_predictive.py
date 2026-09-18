# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Pure weather + thermal helpers for predictive pool heating.

The planner intentionally works at *day* level:
- weather identifies credible bathing days up to 15 days ahead;
- a learned thermal model estimates PAC gain by outdoor temperature/preset;
- a learned passive-loss model estimates overnight cooling;
- recovery is planned backwards from the selected bathing day;
- daytime Smart heating is preferred; Turbo and then night heating are used
  only when needed to keep a future bathing day thermally reachable.

There is deliberately no synthetic "bathing hour" and no hard-coded
morning/afternoon heating slot.
"""

import datetime


_CONDITION_SUN_FACTOR = {
    "sunny": 1.00,
    "clear-night": 0.00,
    "partlycloudy": 0.72,
    "cloudy": 0.30,
    "fog": 0.20,
    "windy": 0.55,
    "windy-variant": 0.45,
    "rainy": 0.10,
    "pouring": 0.00,
    "lightning": 0.05,
    "lightning-rainy": 0.00,
    "hail": 0.00,
    "snowy": 0.05,
    "snowy-rainy": 0.00,
    "exceptional": 0.00,
}

_PRESET_FALLBACK_FACTOR = {
    "eco": 0.82,
    "silent": 0.86,
    "standard": 0.95,
    "smart": 1.00,
    "boost": 1.15,
    "turbo": 1.25,
}


def _number(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value, low, high):
    return max(low, min(high, value))


def _parse_datetime(value):
    if isinstance(value, datetime.datetime):
        return value
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def swim_day_score(
    entry,
    min_air_c=21.0,
    ideal_air_c=26.0,
    wind_penalty_from=15.0,
):
    """Return a 0..100 bathing-opportunity score from daily weather."""
    temperature = _number(entry.get("temperature"))
    if temperature is None:
        return 0.0

    min_air_c = float(min_air_c)
    ideal_air_c = max(min_air_c + 0.1, float(ideal_air_c))
    temp_floor = min_air_c - 4.0
    temp_factor = _clamp(
        (temperature - temp_floor) / (ideal_air_c - temp_floor),
        0.0,
        1.0,
    )
    temperature_score = 55.0 * temp_factor

    condition = str(entry.get("condition") or "").strip().lower()
    condition_factor = _CONDITION_SUN_FACTOR.get(condition, 0.45)
    cloud = _number(entry.get("cloud_coverage"))
    if cloud is None:
        sun_factor = condition_factor
    else:
        cloud_factor = 1.0 - _clamp(cloud, 0.0, 100.0) / 100.0
        sun_factor = 0.70 * condition_factor + 0.30 * cloud_factor
    sun_score = 30.0 * _clamp(sun_factor, 0.0, 1.0)

    precipitation_probability = _number(entry.get("precipitation_probability"))
    rain_probability_penalty = 0.0
    if precipitation_probability is not None:
        rain_probability_penalty = 20.0 * _clamp(
            precipitation_probability, 0.0, 100.0
        ) / 100.0

    precipitation = _number(entry.get("precipitation"))
    rain_amount_penalty = 0.0
    if precipitation is not None:
        rain_amount_penalty = min(15.0, max(0.0, precipitation) * 3.0)

    wind = _number(entry.get("wind_speed"))
    wind_penalty = 0.0
    if wind is not None and wind > wind_penalty_from:
        wind_penalty = min(15.0, (wind - wind_penalty_from) * 0.75)

    return round(
        _clamp(
            temperature_score
            + sun_score
            - rain_probability_penalty
            - rain_amount_penalty
            - wind_penalty,
            0.0,
            100.0,
        ),
        1,
    )


def forecast_horizon_profile(index):
    """Return a confidence label + weighting for a 15-day outlook."""
    index = max(0, int(index))
    if index <= 3:
        return {"confidence": "strong", "weight": 1.00}
    if index <= 7:
        return {"confidence": "medium", "weight": 0.95}
    if index <= 10:
        return {"confidence": "trend", "weight": 0.85}
    return {"confidence": "indicative", "weight": 0.70}


def normalize_daily_forecast(
    raw_forecast,
    horizon_days=15,
    min_air_c=21.0,
    ideal_air_c=26.0,
):
    """Normalize Home Assistant daily forecast entries up to 15 days."""
    result = []
    limit = max(1, min(15, int(horizon_days)))
    for index, raw in enumerate(list(raw_forecast or [])[:limit]):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        raw_score = swim_day_score(
            item,
            min_air_c=min_air_c,
            ideal_air_c=ideal_air_c,
        )
        profile = forecast_horizon_profile(index)
        item["score"] = raw_score
        item["strategic_score"] = round(raw_score * profile["weight"], 1)
        item["horizon_weight"] = profile["weight"]
        item["confidence"] = profile["confidence"]
        item["forecast_index"] = index
        parsed = _parse_datetime(item.get("datetime"))
        item["date"] = parsed.date() if parsed is not None else None
        result.append(item)
    return result


def extract_weather_forecast(service_result, entity_id):
    """Extract a forecast list from AppDaemon/Home Assistant response variants."""
    result = service_result
    if not isinstance(result, dict):
        return []

    nested = result.get("result")
    if isinstance(nested, dict) and isinstance(nested.get("response"), dict):
        result = nested["response"]
    elif isinstance(result.get("response"), dict):
        result = result["response"]

    node = result.get(entity_id) if isinstance(result, dict) else None
    if isinstance(node, dict) and isinstance(node.get("forecast"), list):
        return node["forecast"]
    if isinstance(result.get("forecast"), list):
        return result["forecast"]
    return []


def find_swim_opportunities(
    forecast,
    today,
    score_min=55.0,
    min_air_c=21.0,
):
    """Return credible bathing days; no synthetic bathing time is invented."""
    if isinstance(today, datetime.datetime):
        today = today.date()
    score_min = float(score_min)
    min_air_c = float(min_air_c)
    result = []
    for index, day in enumerate(forecast or []):
        date_value = day.get("date") or (today + datetime.timedelta(days=index))
        if date_value < today:
            continue
        temperature = _number(day.get("temperature"))
        score = _number(day.get("score")) or 0.0
        strategic_score = _number(day.get("strategic_score"))
        if strategic_score is None:
            strategic_score = score * forecast_horizon_profile(index)["weight"]
        if (
            temperature is None
            or temperature < min_air_c
            or strategic_score < score_min
        ):
            continue
        result.append(
            {
                "index": index,
                "date": date_value,
                "temperature": temperature,
                "templow": _number(day.get("templow")),
                "score": score,
                "strategic_score": round(strategic_score, 1),
                "confidence": day.get("confidence")
                or forecast_horizon_profile(index)["confidence"],
                "condition": day.get("condition"),
            }
        )
    return result


def ambient_bin(temperature):
    value = _number(temperature)
    if value is None:
        return "unknown"
    if value < 10:
        return "lt10"
    if value < 15:
        return "10_15"
    if value < 20:
        return "15_20"
    if value < 25:
        return "20_25"
    return "ge25"


def normalize_preset_name(value):
    text = str(value or "smart").strip().casefold()
    return text or "smart"


def update_heating_rate_model(
    model,
    preset,
    ambient_c,
    sample_rate_c_per_h,
    alpha=0.25,
):
    """EWMA learning of real pool heating rate by PAC preset and air bin."""
    rate = _number(sample_rate_c_per_h)
    if rate is None or not (0.03 <= rate <= 1.50):
        return dict(model or {})

    alpha = _clamp(float(alpha), 0.05, 1.0)
    key = normalize_preset_name(preset)
    bucket = ambient_bin(ambient_c)

    result = {
        str(p): {str(b): dict(v) for b, v in (bins or {}).items()}
        for p, bins in (model or {}).items()
    }
    bins = result.setdefault(key, {})
    current = dict(bins.get(bucket) or {})
    previous = _number(current.get("rate"))
    count = int(current.get("count") or 0)
    learned = rate if previous is None else previous + alpha * (rate - previous)
    bins[bucket] = {"rate": round(learned, 4), "count": count + 1}
    return result


def estimate_heating_rate(
    base_rate_c_per_h,
    preset,
    ambient_c=None,
    learned_model=None,
):
    """Estimate net water gain °C/h, preferring learned data for this PAC mode."""
    base = max(0.05, float(base_rate_c_per_h))
    ambient = _number(ambient_c)
    preset_key = normalize_preset_name(preset)

    if ambient is None:
        weather_adjusted = base
    else:
        factor = _clamp(1.0 + 0.018 * (ambient - 20.0), 0.60, 1.30)
        weather_adjusted = base * factor

    weather_adjusted *= _PRESET_FALLBACK_FACTOR.get(preset_key, 1.0)

    learned = ((learned_model or {}).get(preset_key) or {}).get(ambient_bin(ambient))
    learned_rate = _number((learned or {}).get("rate"))
    learned_count = int((learned or {}).get("count") or 0)
    if learned_rate is not None and learned_count > 0:
        weight = min(0.85, 0.35 + 0.10 * learned_count)
        return round(
            weight * learned_rate + (1.0 - weight) * weather_adjusted,
            4,
        )
    return round(weather_adjusted, 4)


def thermal_delta_bin(water_c, ambient_c):
    water = _number(water_c)
    ambient = _number(ambient_c)
    if water is None or ambient is None:
        return "unknown"
    delta = max(0.0, water - ambient)
    if delta < 5:
        return "0_5"
    if delta < 10:
        return "5_10"
    if delta < 15:
        return "10_15"
    if delta < 20:
        return "15_20"
    return "ge20"


def normalize_cover_state(value):
    text = str(value or "").strip().casefold()
    if text in {"closed", "closing", "fermé", "fermee", "fermé(e)", "off"}:
        return "closed"
    if text in {"open", "opening", "ouvert", "ouverte", "on"}:
        return "open"
    return "unknown"


def update_loss_model(
    model,
    cover_state,
    water_c,
    ambient_c,
    sample_loss_c_per_h,
    alpha=0.25,
):
    """EWMA learning of passive night loss °C/h by cover + water/air delta."""
    rate = _number(sample_loss_c_per_h)
    if rate is None or not (0.0 <= rate <= 0.40):
        return dict(model or {})

    alpha = _clamp(float(alpha), 0.05, 1.0)
    cover = normalize_cover_state(cover_state)
    bucket = thermal_delta_bin(water_c, ambient_c)
    result = {
        str(c): {str(b): dict(v) for b, v in (bins or {}).items()}
        for c, bins in (model or {}).items()
    }
    bins = result.setdefault(cover, {})
    current = dict(bins.get(bucket) or {})
    previous = _number(current.get("rate"))
    count = int(current.get("count") or 0)
    learned = rate if previous is None else previous + alpha * (rate - previous)
    bins[bucket] = {"rate": round(learned, 4), "count": count + 1}
    return result


def estimate_loss_rate(
    water_c,
    ambient_c,
    cover_state=None,
    learned_model=None,
    fallback_delta10_c_per_h=0.05,
):
    """Estimate passive pool cooling °C/h."""
    water = _number(water_c)
    ambient = _number(ambient_c)
    if water is None or ambient is None:
        return 0.0

    cover = normalize_cover_state(cover_state)
    bucket = thermal_delta_bin(water, ambient)
    learned = ((learned_model or {}).get(cover) or {}).get(bucket)
    learned_rate = _number((learned or {}).get("rate"))
    learned_count = int((learned or {}).get("count") or 0)

    delta = max(0.0, water - ambient)
    fallback = max(0.0, float(fallback_delta10_c_per_h)) * delta / 10.0
    if cover == "open":
        fallback *= 1.8
    elif cover == "unknown":
        fallback *= 1.25
    fallback = _clamp(fallback, 0.0, 0.30)

    if learned_rate is not None and learned_count > 0:
        weight = min(0.85, 0.35 + 0.10 * learned_count)
        return round(weight * learned_rate + (1.0 - weight) * fallback, 4)
    return round(fallback, 4)


def _forecast_by_date(forecast):
    return {
        item.get("date"): item
        for item in (forecast or [])
        if isinstance(item, dict) and item.get("date") is not None
    }


def _day_temperature(item):
    return _number((item or {}).get("temperature"))


def _night_temperature(item):
    low = _number((item or {}).get("templow"))
    if low is not None:
        return low
    high = _day_temperature(item)
    return None if high is None else high - 5.0


def _candidate_requirements(
    *,
    candidate,
    today,
    water_c,
    target_c,
    forecast,
    loss_model,
    cover_state,
    night_hours,
    loss_fallback_delta10,
):
    """Return target recovery including predicted passive night losses."""
    by_date = _forecast_by_date(forecast)
    total_loss = 0.0
    projected = float(water_c)
    floor_track = []
    cursor = today

    while cursor < candidate["date"]:
        entry = by_date.get(cursor) or {}
        ambient = _night_temperature(entry)
        loss_rate = estimate_loss_rate(
            projected,
            ambient,
            cover_state=cover_state,
            learned_model=loss_model,
            fallback_delta10_c_per_h=loss_fallback_delta10,
        )
        loss = loss_rate * max(0.0, float(night_hours))
        total_loss += loss
        projected -= loss
        floor_track.append((cursor, projected, loss))
        cursor += datetime.timedelta(days=1)

    required_gain = max(0.0, float(target_c) - float(water_c)) + total_loss
    return {
        "required_gain_c": required_gain,
        "predicted_loss_c": total_loss,
        "projected_without_heat_c": projected,
        "floor_track": floor_track,
    }


def _capacity_by_day(
    *,
    today,
    candidate_date,
    forecast,
    preset,
    base_rate,
    heating_model,
    day_hours,
    today_day_hours_remaining,
    include_night=False,
    night_hours=12.0,
):
    by_date = _forecast_by_date(forecast)
    result = []
    cursor = today
    while cursor < candidate_date:
        entry = by_date.get(cursor) or {}
        day_air = _day_temperature(entry)
        usable_day_hours = (
            max(0.0, float(today_day_hours_remaining))
            if cursor == today
            else max(0.0, float(day_hours))
        )
        day_rate = estimate_heating_rate(
            base_rate,
            preset,
            day_air,
            learned_model=heating_model,
        )
        capacity = day_rate * usable_day_hours
        night_capacity = 0.0
        if include_night:
            night_air = _night_temperature(entry)
            night_rate = estimate_heating_rate(
                base_rate,
                preset,
                night_air,
                learned_model=heating_model,
            )
            night_capacity = night_rate * max(0.0, float(night_hours))
            capacity += night_capacity
        result.append(
            {
                "date": cursor,
                "capacity_c": capacity,
                "day_capacity_c": day_rate * usable_day_hours,
                "night_capacity_c": night_capacity,
            }
        )
        cursor += datetime.timedelta(days=1)
    return result


def _latest_start_date(capacities, required_gain_c):
    required = max(0.0, float(required_gain_c))
    if required <= 0.0:
        return capacities[-1]["date"] if capacities else None

    cumulative = 0.0
    for item in reversed(capacities):
        cumulative += max(0.0, float(item.get("capacity_c") or 0.0))
        if cumulative >= required:
            return item["date"]
    return None


def _choose_recovery_strategy(
    *,
    today,
    candidate_date,
    required_gain_c,
    forecast,
    base_rate,
    heating_model,
    smart_preset,
    turbo_preset,
    day_hours,
    today_day_hours_remaining,
    night_hours,
):
    """Prefer Smart/day, then Turbo/day, then night heating as a last resort."""
    attempts = (
        (smart_preset, False),
        (turbo_preset, False),
        (smart_preset, True),
        (turbo_preset, True),
    )
    for preset, include_night in attempts:
        capacities = _capacity_by_day(
            today=today,
            candidate_date=candidate_date,
            forecast=forecast,
            preset=preset,
            base_rate=base_rate,
            heating_model=heating_model,
            day_hours=day_hours,
            today_day_hours_remaining=today_day_hours_remaining,
            include_night=include_night,
            night_hours=night_hours,
        )
        start_date = _latest_start_date(capacities, required_gain_c)
        if start_date is not None:
            total_capacity = sum(x["capacity_c"] for x in capacities)
            return {
                "preset": preset,
                "allow_night": include_night,
                "start_date": start_date,
                "capacity_c": round(total_capacity, 2),
                "capacities": capacities,
            }
    return None


def build_predictive_plan(
    *,
    now,
    water_c,
    target_c,
    forecast,
    base_heating_rate_c_per_h,
    heating_rate_model=None,
    loss_model=None,
    cover_state=None,
    floor_delta_c=2.0,
    minimum_water_c=None,
    floor_recharge_c=0.5,
    stop_margin_c=0.2,
    score_min=55.0,
    min_air_c=21.0,
    smart_preset="Smart",
    turbo_preset="Turbo",
    day_hours=12.0,
    today_day_hours_remaining=12.0,
    night_hours=12.0,
    loss_fallback_delta10_c_per_h=0.05,
    daylight_active=True,
):
    """Build one predictive decision from weather + learned thermal behavior."""
    today = now.date() if isinstance(now, datetime.datetime) else now
    water = float(water_c)
    target = float(target_c)
    stop_margin = max(0.0, float(stop_margin_c))
    if minimum_water_c is None:
        floor_c = target - max(0.0, float(floor_delta_c))
    else:
        floor_c = float(minimum_water_c)
    floor_c = min(target, floor_c)
    floor_target = min(target, floor_c + max(0.1, float(floor_recharge_c)))

    opportunities = find_swim_opportunities(
        forecast,
        today,
        score_min=score_min,
        min_air_c=min_air_c,
    )

    base = {
        "should_heat": False,
        "heat_target_c": None,
        "preset": smart_preset,
        "allow_night": False,
        "floor_c": round(floor_c, 2),
        "floor_target_c": round(floor_target, 2),
        "candidate": None,
        "opportunities": opportunities,
        "recovery_start_date": None,
        "required_gain_c": 0.0,
        "predicted_loss_c": 0.0,
        "projected_without_heat_c": round(water, 2),
        "estimated_capacity_c": 0.0,
        "reason": "",
    }

    by_date = _forecast_by_date(forecast)
    today_entry = by_date.get(today) or {}
    next_night_air = _night_temperature(today_entry)
    next_night_loss = estimate_loss_rate(
        water,
        next_night_air,
        cover_state=cover_state,
        learned_model=loss_model,
        fallback_delta10_c_per_h=loss_fallback_delta10_c_per_h,
    ) * max(0.0, float(night_hours))
    projected_next_morning = water - next_night_loss

    selected = None
    for candidate in opportunities:
        if candidate["date"] == today:
            deficit = max(0.0, target - water)
            day_air = _day_temperature(by_date.get(today) or {})
            smart_rate = estimate_heating_rate(
                base_heating_rate_c_per_h,
                smart_preset,
                day_air,
                learned_model=heating_rate_model,
            )
            turbo_rate = estimate_heating_rate(
                base_heating_rate_c_per_h,
                turbo_preset,
                day_air,
                learned_model=heating_rate_model,
            )
            remaining_hours = max(0.0, float(today_day_hours_remaining))
            if deficit <= smart_rate * remaining_hours:
                selected = (
                    candidate,
                    {
                        "preset": smart_preset,
                        "allow_night": False,
                        "start_date": today,
                        "capacity_c": smart_rate * remaining_hours,
                    },
                    {
                        "required_gain_c": deficit,
                        "predicted_loss_c": 0.0,
                        "projected_without_heat_c": water,
                        "floor_track": [],
                    },
                )
                break
            if deficit <= turbo_rate * remaining_hours:
                selected = (
                    candidate,
                    {
                        "preset": turbo_preset,
                        "allow_night": False,
                        "start_date": today,
                        "capacity_c": turbo_rate * remaining_hours,
                    },
                    {
                        "required_gain_c": deficit,
                        "predicted_loss_c": 0.0,
                        "projected_without_heat_c": water,
                        "floor_track": [],
                    },
                )
                break
            continue

        requirements = _candidate_requirements(
            candidate=candidate,
            today=today,
            water_c=water,
            target_c=target,
            forecast=forecast,
            loss_model=loss_model,
            cover_state=cover_state,
            night_hours=night_hours,
            loss_fallback_delta10=loss_fallback_delta10_c_per_h,
        )
        strategy = _choose_recovery_strategy(
            today=today,
            candidate_date=candidate["date"],
            required_gain_c=requirements["required_gain_c"],
            forecast=forecast,
            base_rate=base_heating_rate_c_per_h,
            heating_model=heating_rate_model,
            smart_preset=smart_preset,
            turbo_preset=turbo_preset,
            day_hours=day_hours,
            today_day_hours_remaining=today_day_hours_remaining,
            night_hours=night_hours,
        )
        if strategy is not None:
            selected = (candidate, strategy, requirements)
            break

    if selected is None:
        if water <= floor_c - stop_margin:
            base.update(
                should_heat=True,
                heat_target_c=round(floor_target, 2),
                allow_night=not bool(daylight_active),
                reason=f"protection plancher {floor_c:.1f} °C",
            )
            return base
        if projected_next_morning < floor_c and daylight_active:
            base.update(
                should_heat=True,
                heat_target_c=round(
                    min(
                        target,
                        floor_target + max(0.0, floor_c - projected_next_morning),
                    ),
                    2,
                ),
                reason=(
                    f"préservation plancher avant nuit "
                    f"(prévision {projected_next_morning:.1f} °C)"
                ),
            )
            return base
        base["reason"] = (
            f"aucune baignade thermiquement pertinente; PAC arrêtée "
            f"(plancher {floor_c:.1f} °C)"
        )
        return base

    candidate, strategy, requirements = selected
    start_date = strategy["start_date"]

    floor_start = None
    for day, projected, _loss in requirements.get("floor_track") or []:
        if projected < floor_c:
            floor_start = day
            break
    if floor_start is not None and (start_date is None or floor_start < start_date):
        start_date = floor_start

    base.update(
        candidate=candidate,
        recovery_start_date=start_date,
        preset=strategy["preset"],
        allow_night=bool(strategy["allow_night"]),
        required_gain_c=round(requirements["required_gain_c"], 2),
        predicted_loss_c=round(requirements["predicted_loss_c"], 2),
        projected_without_heat_c=round(
            requirements["projected_without_heat_c"], 2
        ),
        estimated_capacity_c=round(strategy.get("capacity_c") or 0.0, 2),
    )

    if water >= target - stop_margin:
        base["reason"] = (
            f"eau prête pour {candidate['date'].isoformat()} "
            f"(score {candidate['strategic_score']:.0f})"
        )
        return base

    if today < start_date:
        if projected_next_morning < floor_c and daylight_active:
            base.update(
                should_heat=True,
                heat_target_c=round(floor_target, 2),
                preset=smart_preset,
                reason=(
                    f"préservation plancher avant préparation "
                    f"{candidate['date'].isoformat()}"
                ),
            )
            return base
        base["reason"] = (
            f"attente; préparation {candidate['date'].isoformat()} "
            f"à partir du {start_date.isoformat()}"
        )
        return base

    if candidate["date"] == today:
        if daylight_active or strategy["allow_night"]:
            base.update(
                should_heat=True,
                heat_target_c=round(target, 2),
                reason=(
                    f"journée baignade {candidate['strategic_score']:.0f}/100; "
                    f"chauffe/maintien {strategy['preset']}"
                ),
            )
        else:
            base["reason"] = "journée baignade; attente du jour"
        return base

    if daylight_active or strategy["allow_night"]:
        base.update(
            should_heat=True,
            heat_target_c=round(target, 2),
            reason=(
                f"préparation {candidate['date'].isoformat()} depuis "
                f"{start_date.isoformat()} • {strategy['preset']}"
                + (" • nuit autorisée" if strategy["allow_night"] else "")
            ),
        )
    else:
        base["reason"] = (
            f"préparation {candidate['date'].isoformat()} en journée; "
            "PAC arrêtée cette nuit"
        )
    return base


def dashboard_forecast(forecast, plan, today):
    """Return compact JSON-serializable rows for a Home Assistant dashboard."""
    if isinstance(today, datetime.datetime):
        today = today.date()
    candidate = (plan or {}).get("candidate") or {}
    selected_date = candidate.get("date")
    start_date = (plan or {}).get("recovery_start_date")
    rows = []
    for index, day in enumerate(forecast or []):
        day_date = day.get("date") or (today + datetime.timedelta(days=index))
        preheat = bool(
            start_date is not None
            and selected_date is not None
            and start_date <= day_date < selected_date
        )
        rows.append(
            {
                "offset": index,
                "label": "J0" if index == 0 else f"J+{index}",
                "date": day_date.isoformat(),
                "temperature": _number(day.get("temperature")),
                "templow": _number(day.get("templow")),
                "condition": day.get("condition"),
                "precipitation_probability": _number(
                    day.get("precipitation_probability")
                ),
                "wind_speed": _number(day.get("wind_speed")),
                "score": _number(day.get("score")) or 0.0,
                "strategic_score": _number(day.get("strategic_score")) or 0.0,
                "confidence": day.get("confidence")
                or forecast_horizon_profile(index)["confidence"],
                "swim": day_date == selected_date,
                "preheat": preheat,
                "heating": preheat or day_date == selected_date,
                "preset": (plan or {}).get("preset") if preheat else None,
                "night_allowed": (
                    bool((plan or {}).get("allow_night")) if preheat else False
                ),
            }
        )
    return rows
