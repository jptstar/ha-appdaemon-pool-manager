# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Pure weather and thermal helpers for adaptive pool heating.

The public planner deliberately returns a *daily decision* rather than a heating
calendar.  Its job is to answer four questions:

1. Is there a bathing window worth preparing for?
2. How warm does the pool need to be *today* so that window remains reachable?
3. Is Smart sufficient, is Turbo necessary, or is exceptional night recovery
   unavoidable?
4. If no useful bathing window exists, can the PAC stay off without making the
   pool impractical to recover?

All performance numbers can be replaced progressively by learned observations
from the actual installation.
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
    """Return a conservative 0..100 bathing score."""
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
    """Distance confidence for a 15-day outlook."""
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
    """Normalize Home Assistant daily forecast entries."""
    result = []
    limit = max(1, min(15, int(horizon_days)))
    for index, raw in enumerate(list(raw_forecast or [])[:limit]):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        score = swim_day_score(
            item,
            min_air_c=min_air_c,
            ideal_air_c=ideal_air_c,
        )
        profile = forecast_horizon_profile(index)
        parsed = _parse_datetime(item.get("datetime"))
        item["date"] = parsed.date() if parsed is not None else None
        item["score"] = score
        item["strategic_score"] = round(score * profile["weight"], 1)
        item["usage_score"] = item["strategic_score"]
        item["horizon_weight"] = profile["weight"]
        item["confidence"] = profile["confidence"]
        item["forecast_index"] = index
        result.append(item)
    return result


def normalize_hourly_forecast(
    raw_forecast,
    min_air_c=21.0,
    ideal_air_c=26.0,
):
    """Normalize hourly forecast entries when the weather provider supports it."""
    result = []
    for raw in list(raw_forecast or []):
        if not isinstance(raw, dict):
            continue
        parsed = _parse_datetime(raw.get("datetime"))
        if parsed is None:
            continue
        item = dict(raw)
        item["parsed_datetime"] = parsed
        item["date"] = parsed.date()
        item["score"] = swim_day_score(
            item,
            min_air_c=min_air_c,
            ideal_air_c=ideal_air_c,
        )
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


def _average(values):
    clean = [float(v) for v in values if _number(v) is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def enrich_daily_with_hourly(daily_forecast, hourly_forecast):
    """Add near-term hourly context to daily rows.

    Weekdays prioritize 16:00-20:00 because that is the normal after-work
    bathing period. Weekends prioritize 11:00-20:00.  Heating performance uses
    the average 08:00-20:00 outdoor temperature instead of the daily maximum.
    """
    rows = [dict(item) for item in (daily_forecast or [])]
    hourly_by_date = {}
    for item in hourly_forecast or []:
        dt = item.get("parsed_datetime") or _parse_datetime(item.get("datetime"))
        if dt is None:
            continue
        hourly_by_date.setdefault(dt.date(), []).append((dt, item))

    for row in rows:
        day = row.get("date")
        samples = hourly_by_date.get(day) or []
        if not samples:
            continue

        daytime = [
            _number(item.get("temperature"))
            for dt, item in samples
            if 8 <= dt.hour < 20
        ]
        night = [
            _number(item.get("temperature"))
            for dt, item in samples
            if dt.hour >= 20 or dt.hour < 8
        ]
        usage_start = 11 if day.weekday() >= 5 else 16
        usage = [
            _number(item.get("score"))
            for dt, item in samples
            if usage_start <= dt.hour < 20
        ]
        usage_temp = [
            _number(item.get("temperature"))
            for dt, item in samples
            if usage_start <= dt.hour < 20
        ]

        day_temp = _average(daytime)
        night_temp = _average(night)
        usage_score = _average(usage)
        usage_temperature = _average(usage_temp)

        if day_temp is not None:
            row["heating_temperature"] = round(day_temp, 2)
        if night_temp is not None:
            row["night_heating_temperature"] = round(night_temp, 2)
        if usage_score is not None:
            weight = _number(row.get("horizon_weight")) or 1.0
            row["usage_score"] = round(usage_score * weight, 1)
        if usage_temperature is not None:
            row["usage_temperature"] = round(usage_temperature, 1)
        row["usage_window"] = "11:00-20:00" if day.weekday() >= 5 else "16:00-20:00"
    return rows


def find_swim_opportunities(
    forecast,
    today,
    score_min=55.0,
    min_air_c=21.0,
):
    """Return credible bathing days ordered by practical usefulness.

    A marginal earlier day is not automatically preferred over a much better
    day immediately after it. Weekends receive a moderate usage bonus.
    """
    if isinstance(today, datetime.datetime):
        today = today.date()

    result = []
    for index, day in enumerate(forecast or []):
        date_value = day.get("date") or (today + datetime.timedelta(days=index))
        if date_value < today:
            continue

        temperature = _number(day.get("usage_temperature"))
        if temperature is None:
            temperature = _number(day.get("temperature"))

        score = _number(day.get("score")) or 0.0
        strategic = _number(day.get("strategic_score"))
        if strategic is None:
            strategic = score * forecast_horizon_profile(index)["weight"]
        usage = _number(day.get("usage_score"))
        if usage is None:
            usage = strategic

        if temperature is None or temperature < float(min_air_c) or usage < float(score_min):
            continue

        weekend_bonus = 10.0 if date_value.weekday() >= 5 else 0.0
        distance_penalty = min(12.0, max(0, index) * 1.25)
        utility = usage + weekend_bonus - distance_penalty

        result.append(
            {
                "index": index,
                "date": date_value,
                "temperature": temperature,
                "templow": _number(day.get("templow")),
                "score": score,
                "strategic_score": round(strategic, 1),
                "usage_score": round(usage, 1),
                "utility": round(utility, 1),
                "weekend": date_value.weekday() >= 5,
                "confidence": day.get("confidence")
                or forecast_horizon_profile(index)["confidence"],
                "condition": day.get("condition"),
                "usage_window": day.get("usage_window"),
            }
        )

    # Search a practical near-term window first; within it choose the best
    # opportunity. This avoids waiting ten days for a tiny score improvement.
    if not result:
        return []
    earliest_index = min(x["index"] for x in result)
    practical = [x for x in result if x["index"] <= earliest_index + 3]
    practical.sort(key=lambda x: (-x["utility"], x["index"]))
    remaining = [x for x in result if x not in practical]
    remaining.sort(key=lambda x: (x["index"], -x["utility"]))
    return practical + remaining


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
    sample_power_w=None,
):
    """EWMA learning of PAC gain and optional electrical power."""
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

    entry = {"rate": round(learned, 4), "count": count + 1}
    power = _number(sample_power_w)
    if power is not None and 50 <= power <= 10000:
        previous_power = _number(current.get("power_w"))
        learned_power = (
            power
            if previous_power is None
            else previous_power + alpha * (power - previous_power)
        )
        entry["power_w"] = round(learned_power, 1)
        entry["kwh_per_c"] = round((learned_power / 1000.0) / max(0.03, learned), 3)
    elif current.get("power_w") is not None:
        entry["power_w"] = current.get("power_w")
        if current.get("kwh_per_c") is not None:
            entry["kwh_per_c"] = current.get("kwh_per_c")

    bins[bucket] = entry
    return result


def estimate_heating_rate(
    base_rate_c_per_h,
    preset,
    ambient_c=None,
    learned_model=None,
):
    """Estimate net water gain °C/h, preferring learned installation data."""
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
        weight = min(0.90, 0.35 + 0.10 * learned_count)
        return round(
            weight * learned_rate + (1.0 - weight) * weather_adjusted,
            4,
        )
    return round(weather_adjusted, 4)


def estimate_heating_power(preset, ambient_c=None, learned_model=None):
    learned = (
        ((learned_model or {}).get(normalize_preset_name(preset)) or {})
        .get(ambient_bin(ambient_c))
    )
    return _number((learned or {}).get("power_w"))


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
    """EWMA learning of passive loss by cover state and water/air delta."""
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
    """Estimate passive water loss °C/h."""
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
        weight = min(0.90, 0.35 + 0.10 * learned_count)
        return round(weight * learned_rate + (1.0 - weight) * fallback, 4)
    return round(fallback, 4)


def _forecast_by_date(forecast):
    return {
        item.get("date"): item
        for item in (forecast or [])
        if isinstance(item, dict) and item.get("date") is not None
    }


def _day_temperature(item):
    value = _number((item or {}).get("heating_temperature"))
    if value is not None:
        return value
    return _number((item or {}).get("temperature"))


def _night_temperature(item):
    value = _number((item or {}).get("night_heating_temperature"))
    if value is not None:
        return value
    low = _number((item or {}).get("templow"))
    if low is not None:
        return low
    high = _day_temperature(item)
    return None if high is None else high - 5.0


def _predicted_loss_path(
    *,
    water_c,
    start_date,
    end_date,
    forecast,
    loss_model,
    cover_state,
    night_hours,
    loss_fallback_delta10,
):
    """Simulate passive night cooling with the projected water temperature."""
    by_date = _forecast_by_date(forecast)
    projected = float(water_c)
    total = 0.0
    rows = []
    cursor = start_date
    while cursor < end_date:
        entry = by_date.get(cursor) or {}
        ambient = _night_temperature(entry)
        rate = estimate_loss_rate(
            projected,
            ambient,
            cover_state=cover_state,
            learned_model=loss_model,
            fallback_delta10_c_per_h=loss_fallback_delta10,
        )
        loss = rate * max(0.0, float(night_hours))
        projected -= loss
        total += loss
        rows.append(
            {
                "date": cursor,
                "ambient_c": ambient,
                "loss_c": round(loss, 3),
                "projected_water_c": round(projected, 3),
            }
        )
        cursor += datetime.timedelta(days=1)
    return total, projected, rows


def _day_capacity(
    *,
    date,
    forecast,
    preset,
    hours,
    base_rate,
    heating_model,
):
    entry = _forecast_by_date(forecast).get(date) or {}
    ambient = _day_temperature(entry)
    rate = estimate_heating_rate(
        base_rate,
        preset,
        ambient,
        learned_model=heating_model,
    )
    return {
        "date": date,
        "ambient_c": ambient,
        "hours": max(0.0, float(hours)),
        "rate_c_per_h": rate,
        "capacity_c": rate * max(0.0, float(hours)),
    }


def _future_smart_capacity(
    *,
    today,
    candidate_date,
    forecast,
    smart_preset,
    base_rate,
    heating_model,
    day_hours,
    candidate_day_hours,
):
    """Smart capacity available *after today* before normal bathing time."""
    total = 0.0
    rows = []
    cursor = today + datetime.timedelta(days=1)
    while cursor < candidate_date:
        item = _day_capacity(
            date=cursor,
            forecast=forecast,
            preset=smart_preset,
            hours=day_hours,
            base_rate=base_rate,
            heating_model=heating_model,
        )
        rows.append(item)
        total += item["capacity_c"]
        cursor += datetime.timedelta(days=1)

    if candidate_date > today and candidate_day_hours > 0:
        item = _day_capacity(
            date=candidate_date,
            forecast=forecast,
            preset=smart_preset,
            hours=candidate_day_hours,
            base_rate=base_rate,
            heating_model=heating_model,
        )
        rows.append(item)
        total += item["capacity_c"]
    return total, rows


def _recovery_start_date(
    *,
    today,
    candidate_date,
    required_gain_c,
    forecast,
    smart_preset,
    base_rate,
    heating_model,
    day_hours,
    candidate_day_hours,
):
    """Latest calendar day from which Smart daytime capacity can cover recovery."""
    required = max(0.0, float(required_gain_c))
    if required <= 0:
        return candidate_date

    cumulative = 0.0
    cursor = candidate_date
    first = True
    while cursor >= today:
        hours = candidate_day_hours if first else day_hours
        first = False
        item = _day_capacity(
            date=cursor,
            forecast=forecast,
            preset=smart_preset,
            hours=hours,
            base_rate=base_rate,
            heating_model=heating_model,
        )
        cumulative += item["capacity_c"]
        if cumulative >= required:
            return cursor
        cursor -= datetime.timedelta(days=1)
    return today


def _candidate_plan(
    *,
    candidate,
    today,
    water,
    target,
    forecast,
    base_rate,
    heating_model,
    loss_model,
    cover_state,
    smart_preset,
    turbo_preset,
    day_hours,
    today_day_hours_remaining,
    candidate_day_hours,
    night_hours,
    loss_fallback_delta10,
    daylight_active,
    floor_c,
    stop_margin,
):
    loss_total, projected_no_heat, loss_rows = _predicted_loss_path(
        water_c=water,
        start_date=today,
        end_date=candidate["date"],
        forecast=forecast,
        loss_model=loss_model,
        cover_state=cover_state,
        night_hours=night_hours,
        loss_fallback_delta10=loss_fallback_delta10,
    )

    required_gain = max(0.0, target - water) + loss_total
    future_smart, future_rows = _future_smart_capacity(
        today=today,
        candidate_date=candidate["date"],
        forecast=forecast,
        smart_preset=smart_preset,
        base_rate=base_rate,
        heating_model=heating_model,
        day_hours=day_hours,
        candidate_day_hours=candidate_day_hours,
    )

    # The trajectory target is the minimum water temperature we need *today* so
    # future Smart daytime capacity can still reach the bathing target.
    trajectory_target = _clamp(
        target + loss_total - future_smart,
        floor_c,
        target,
    )
    gain_today = max(0.0, trajectory_target - water)

    today_smart = _day_capacity(
        date=today,
        forecast=forecast,
        preset=smart_preset,
        hours=today_day_hours_remaining,
        base_rate=base_rate,
        heating_model=heating_model,
    )
    today_turbo = _day_capacity(
        date=today,
        forecast=forecast,
        preset=turbo_preset,
        hours=today_day_hours_remaining,
        base_rate=base_rate,
        heating_model=heating_model,
    )

    day_preset = smart_preset
    night_required_c = 0.0
    night_preset = smart_preset
    if gain_today > today_smart["capacity_c"] + stop_margin:
        day_preset = turbo_preset
    remaining_after_day = max(
        0.0,
        gain_today - (
            today_smart["capacity_c"]
            if day_preset == smart_preset
            else today_turbo["capacity_c"]
        ),
    )

    if remaining_after_day > stop_margin:
        entry = _forecast_by_date(forecast).get(today) or {}
        night_air = _night_temperature(entry)
        smart_night_rate = estimate_heating_rate(
            base_rate,
            smart_preset,
            night_air,
            learned_model=heating_model,
        )
        turbo_night_rate = estimate_heating_rate(
            base_rate,
            turbo_preset,
            night_air,
            learned_model=heating_model,
        )
        smart_night_capacity = smart_night_rate * max(0.0, float(night_hours))
        night_required_c = remaining_after_day
        if remaining_after_day > smart_night_capacity + stop_margin:
            night_preset = turbo_preset

    start_date = _recovery_start_date(
        today=today,
        candidate_date=candidate["date"],
        required_gain_c=required_gain,
        forecast=forecast,
        smart_preset=smart_preset,
        base_rate=base_rate,
        heating_model=heating_model,
        day_hours=day_hours,
        candidate_day_hours=candidate_day_hours,
    )

    # Absolute reachability check: even the exceptional strategy (Turbo by
    # day + Turbo at night) must be able to cover the full recovery. This keeps
    # the planner from advertising an attractive but physically impossible day.
    max_capacity = 0.0
    cursor = today
    by_date = _forecast_by_date(forecast)
    while cursor <= candidate["date"]:
        if cursor == today:
            hours = today_day_hours_remaining
        elif cursor == candidate["date"]:
            hours = candidate_day_hours
        else:
            hours = day_hours

        turbo_day = _day_capacity(
            date=cursor,
            forecast=forecast,
            preset=turbo_preset,
            hours=hours,
            base_rate=base_rate,
            heating_model=heating_model,
        )
        max_capacity += turbo_day["capacity_c"]

        if cursor < candidate["date"]:
            night_air = _night_temperature(by_date.get(cursor) or {})
            max_capacity += estimate_heating_rate(
                base_rate,
                turbo_preset,
                night_air,
                learned_model=heating_model,
            ) * max(0.0, float(night_hours))
        cursor += datetime.timedelta(days=1)

    thermally_reachable = required_gain <= max_capacity + stop_margin

    return {
        "candidate": candidate,
        "required_gain_c": required_gain,
        "predicted_loss_c": loss_total,
        "projected_without_heat_c": projected_no_heat,
        "loss_path": loss_rows,
        "future_smart_capacity_c": future_smart,
        "future_capacity": future_rows,
        "trajectory_target_c": trajectory_target,
        "gain_today_c": gain_today,
        "day_preset": day_preset,
        "night_required_c": night_required_c,
        "night_preset": night_preset,
        "recovery_start_date": start_date,
        "today_smart_capacity_c": today_smart["capacity_c"],
        "today_turbo_capacity_c": today_turbo["capacity_c"],
        "thermally_reachable": thermally_reachable,
        "max_recovery_capacity_c": max_capacity,
        "daylight_active": bool(daylight_active),
    }


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
    candidate_day_hours=6.0,
    night_hours=12.0,
    loss_fallback_delta10_c_per_h=0.05,
    daylight_active=True,
):
    """Return one decisive action: wait, preserve, preheat or maintain."""
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
        "action": "WAIT",
        "should_heat": False,
        "heat_target_c": None,
        "preset": smart_preset,
        "night_heating": False,
        "night_required_c": 0.0,
        "floor_c": round(floor_c, 2),
        "floor_target_c": round(floor_target, 2),
        "candidate": None,
        "opportunities": opportunities,
        "recovery_start_date": None,
        "trajectory_target_c": round(floor_c, 2),
        "required_gain_c": 0.0,
        "predicted_loss_c": 0.0,
        "projected_without_heat_c": round(water, 2),
        "future_smart_capacity_c": 0.0,
        "thermal_margin_c": 0.0,
        "reason": "",
    }

    selected = None
    for candidate in opportunities:
        candidate_plan = _candidate_plan(
            candidate=candidate,
            today=today,
            water=water,
            target=target,
            forecast=forecast,
            base_rate=base_heating_rate_c_per_h,
            heating_model=heating_rate_model,
            loss_model=loss_model,
            cover_state=cover_state,
            smart_preset=smart_preset,
            turbo_preset=turbo_preset,
            day_hours=day_hours,
            today_day_hours_remaining=today_day_hours_remaining,
            candidate_day_hours=candidate_day_hours,
            night_hours=night_hours,
            loss_fallback_delta10=loss_fallback_delta10_c_per_h,
            daylight_active=daylight_active,
            floor_c=floor_c,
            stop_margin=stop_margin,
        )
        # A weather-friendly day is useful only if the learned installation can
        # physically recover the requested water temperature in time.
        if not candidate_plan["thermally_reachable"]:
            continue
        selected = candidate_plan
        break

    by_date = _forecast_by_date(forecast)
    tonight_air = _night_temperature(by_date.get(today) or {})
    next_night_loss = estimate_loss_rate(
        water,
        tonight_air,
        cover_state=cover_state,
        learned_model=loss_model,
        fallback_delta10_c_per_h=loss_fallback_delta10_c_per_h,
    ) * max(0.0, float(night_hours))
    projected_next_morning = water - next_night_loss

    if selected is None:
        if water <= floor_c - stop_margin or projected_next_morning < floor_c:
            target_floor = min(
                target,
                floor_target + max(0.0, floor_c - projected_next_morning),
            )
            base.update(
                action="PRESERVE",
                should_heat=True,
                heat_target_c=round(target_floor, 2),
                preset=smart_preset,
                night_heating=not bool(daylight_active),
                trajectory_target_c=round(target_floor, 2),
                predicted_loss_c=round(next_night_loss, 2),
                projected_without_heat_c=round(projected_next_morning, 2),
                reason=(
                    f"préserver le plancher {floor_c:.1f} °C; "
                    f"projection matin {projected_next_morning:.1f} °C"
                ),
            )
            return base

        base["reason"] = (
            f"météo peu intéressante; PAC arrêtée tant que l'eau reste "
            f"récupérable au-dessus de {floor_c:.1f} °C"
        )
        return base

    candidate = selected["candidate"]
    trajectory = selected["trajectory_target_c"]
    thermal_margin = (
        selected["future_smart_capacity_c"]
        - max(0.0, target - water)
        - selected["predicted_loss_c"]
    )

    base.update(
        candidate=candidate,
        recovery_start_date=selected["recovery_start_date"],
        trajectory_target_c=round(trajectory, 2),
        required_gain_c=round(selected["required_gain_c"], 2),
        predicted_loss_c=round(selected["predicted_loss_c"], 2),
        projected_without_heat_c=round(selected["projected_without_heat_c"], 2),
        future_smart_capacity_c=round(selected["future_smart_capacity_c"], 2),
        thermal_margin_c=round(thermal_margin, 2),
        night_required_c=round(selected["night_required_c"], 2),
    )

    # On the selected bathing day, keep the requested water target. There is no
    # 16:00 expiry: a good weekday remains relevant through the normal after-work
    # usage window and a weekend remains relevant throughout the day.
    if candidate["date"] == today:
        if water >= target - stop_margin:
            base.update(
                action="MAINTAIN",
                should_heat=False,
                heat_target_c=round(target, 2),
                preset=smart_preset,
                reason=(
                    f"journée baignade prioritaire; eau prête à {water:.1f} °C"
                ),
            )
            return base

        preset = selected["day_preset"] if daylight_active else selected["night_preset"]
        base.update(
            action="MAINTAIN",
            should_heat=True,
            heat_target_c=round(target, 2),
            preset=preset,
            night_heating=not bool(daylight_active),
            reason=(
                f"journée baignade {candidate['usage_score']:.0f}/100; "
                f"rattrapage/maintien {preset}"
            ),
        )
        return base

    # Future opportunity: heat only to today's trajectory target, never straight
    # to the final setpoint just because a good day exists several days away.
    if water >= trajectory - stop_margin:
        base.update(
            action="WAIT",
            should_heat=False,
            reason=(
                f"trajectoire suffisante pour {candidate['date'].isoformat()}; "
                f"{water:.1f} ≥ {trajectory:.1f} °C"
            ),
        )
        return base

    if daylight_active:
        preset = selected["day_preset"]
        base.update(
            action="PREHEAT",
            should_heat=True,
            heat_target_c=round(trajectory, 2),
            preset=preset,
            night_heating=False,
            reason=(
                f"préparation {candidate['date'].isoformat()}; "
                f"viser {trajectory:.1f} °C aujourd'hui en {preset}"
            ),
        )
        return base

    # At night, only continue if the day was not enough to keep the trajectory.
    if selected["night_required_c"] > stop_margin:
        preset = selected["night_preset"]
        base.update(
            action="PREHEAT",
            should_heat=True,
            heat_target_c=round(trajectory, 2),
            preset=preset,
            night_heating=True,
            reason=(
                f"rattrapage nocturne ponctuel nécessaire "
                f"({selected['night_required_c']:.1f} °C) pour "
                f"{candidate['date'].isoformat()}"
            ),
        )
        return base

    base.update(
        action="WAIT",
        should_heat=False,
        reason=(
            f"nuit: trajectoire récupérable demain pour "
            f"{candidate['date'].isoformat()}"
        ),
    )
    return base


def dashboard_forecast(forecast, plan, today):
    """Return compact JSON-serializable rows for Home Assistant dashboards."""
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
                "usage_temperature": _number(day.get("usage_temperature")),
                "templow": _number(day.get("templow")),
                "condition": day.get("condition"),
                "precipitation_probability": _number(
                    day.get("precipitation_probability")
                ),
                "wind_speed": _number(day.get("wind_speed")),
                "score": _number(day.get("score")) or 0.0,
                "strategic_score": _number(day.get("strategic_score")) or 0.0,
                "usage_score": _number(day.get("usage_score")) or 0.0,
                "confidence": day.get("confidence")
                or forecast_horizon_profile(index)["confidence"],
                "weekend": day_date.weekday() >= 5,
                "usage_window": day.get("usage_window"),
                "swim": day_date == selected_date,
                "preheat": preheat,
                "heating": bool(preheat or day_date == selected_date),
            }
        )
    return rows
