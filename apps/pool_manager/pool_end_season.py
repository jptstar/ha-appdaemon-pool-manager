# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Pure helpers for predictive end-of-season pool heating."""

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


def _parse_time(value, default="16:00:00"):
    text = str(value or default).strip()
    try:
        return datetime.time.fromisoformat(text)
    except ValueError:
        return datetime.time.fromisoformat(default)


def swim_day_score(
    entry,
    min_air_c=21.0,
    ideal_air_c=26.0,
    wind_penalty_from=15.0,
):
    """Return a conservative 0..100 bathing-opportunity score.

    Temperature and sun dominate. Rain probability/amount and strong wind reduce
    the score. Missing optional fields are simply ignored rather than guessed.
    """
    temperature = _number(entry.get("temperature"))
    if temperature is None:
        return 0.0

    min_air_c = float(min_air_c)
    ideal_air_c = max(min_air_c + 0.1, float(ideal_air_c))

    # A day a few degrees below the configured bathing minimum can still receive
    # some score, but cannot become a candidate unless it also meets min_air_c.
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


def normalize_daily_forecast(
    raw_forecast,
    horizon_days=10,
    min_air_c=21.0,
    ideal_air_c=26.0,
):
    """Normalize Home Assistant daily weather forecast entries."""
    result = []
    for raw in list(raw_forecast or [])[: max(1, int(horizon_days))]:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["score"] = swim_day_score(
            item,
            min_air_c=min_air_c,
            ideal_air_c=ideal_air_c,
        )
        parsed = _parse_datetime(item.get("datetime"))
        item["date"] = parsed.date() if parsed is not None else None
        result.append(item)
    return result


def extract_weather_forecast(service_result, entity_id):
    """Extract a forecast list from AppDaemon/Home Assistant response variants."""
    result = service_result
    if not isinstance(result, dict):
        return []

    # AppDaemon versions/plugins can wrap the HA response differently.
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


def _target_datetime(day, target_time, timezone_info=None):
    value = datetime.datetime.combine(day, target_time)
    if timezone_info is not None:
        value = value.replace(tzinfo=timezone_info)
    return value


def find_next_swim_window(
    forecast,
    now,
    score_min=55.0,
    min_air_c=21.0,
    swim_time="16:00:00",
):
    """Return the earliest credible bathing day and whether it is a last chance."""
    target_time = _parse_time(swim_time)
    score_min = float(score_min)
    min_air_c = float(min_air_c)

    candidates = []
    for index, day in enumerate(forecast or []):
        date_value = day.get("date")
        if date_value is None:
            date_value = now.date() + datetime.timedelta(days=index)
        target_dt = _target_datetime(date_value, target_time, now.tzinfo)
        temperature = _number(day.get("temperature"))
        score = _number(day.get("score")) or 0.0
        if target_dt <= now:
            continue
        if temperature is None or temperature < min_air_c or score < score_min:
            continue
        candidates.append((index, day, target_dt))

    if not candidates:
        return None

    index, day, target_dt = candidates[0]
    bad_streak = 0
    for following in (forecast or [])[index + 1 : index + 4]:
        temperature = _number(following.get("temperature"))
        score = _number(following.get("score")) or 0.0
        if temperature is not None and temperature >= min_air_c and score >= score_min:
            break
        bad_streak += 1

    return {
        "index": index,
        "date": target_dt.date(),
        "target_datetime": target_dt,
        "temperature": _number(day.get("temperature")),
        "score": _number(day.get("score")) or 0.0,
        "condition": day.get("condition"),
        "bad_streak_after": bad_streak,
        "last_chance": bad_streak >= 2,
    }


def _window_for_day(day, start_time, end_time, tzinfo):
    start = _target_datetime(day, start_time, tzinfo)
    end = _target_datetime(day, end_time, tzinfo)
    if end <= start:
        end += datetime.timedelta(days=1)
    return start, end


def latest_preferred_start(
    target_datetime,
    required_hours,
    window_start="08:00:00",
    window_end="20:00:00",
):
    """Schedule required heating hours backwards inside preferred daytime windows.

    If the returned datetime is already in the past, the plan is overdue and
    heating should start immediately, even outside the preferred window.
    """
    remaining = max(0.0, float(required_hours))
    if remaining <= 0.0:
        return target_datetime

    start_time = _parse_time(window_start, "08:00:00")
    end_time = _parse_time(window_end, "20:00:00")
    cursor_day = target_datetime.date()
    cursor = target_datetime

    # Ten days of forecast plus a little margin is enough for this planner.
    for _ in range(14):
        start, end = _window_for_day(cursor_day, start_time, end_time, target_datetime.tzinfo)
        segment_end = min(cursor, end)
        if segment_end > start:
            available = (segment_end - start).total_seconds() / 3600.0
            if remaining <= available:
                return segment_end - datetime.timedelta(hours=remaining)
            remaining -= available
        cursor_day -= datetime.timedelta(days=1)
        cursor = _target_datetime(cursor_day, end_time, target_datetime.tzinfo)

    # More heating is required than all considered preferred windows can provide.
    return target_datetime - datetime.timedelta(days=14)


def _inside_preferred_window(now, start="08:00:00", end="20:00:00"):
    start_time = _parse_time(start, "08:00:00")
    end_time = _parse_time(end, "20:00:00")
    current = now.timetz().replace(tzinfo=None)
    if start_time <= end_time:
        return start_time <= current < end_time
    return current >= start_time or current < end_time


def build_end_season_plan(
    *,
    now,
    water_c,
    target_c,
    forecast,
    score_min=55.0,
    min_air_c=21.0,
    swim_time="16:00:00",
    heating_rate_c_per_h=0.30,
    floor_delta_c=3.0,
    maintenance_band_c=0.5,
    stop_margin_c=0.2,
    safety_margin_h=0.5,
    last_chance_margin_h=1.0,
    heating_window_start="08:00:00",
    heating_window_end="20:00:00",
):
    """Build one end-of-season heating decision.

    Strategy:
    - look for the next credible bathing opportunity across the whole forecast;
    - schedule only the heating hours needed to be ready for that opportunity;
    - prefer the latest daytime hours before the deadline, avoiding needless
      overnight heating before a warm/sunny day;
    - if no opportunity exists, keep only a recovery floor so the pool does not
      become impossible to reheat when the forecast changes.
    """
    water_c = float(water_c)
    target_c = float(target_c)
    heating_rate = max(0.05, float(heating_rate_c_per_h))
    floor_c = target_c - max(0.0, float(floor_delta_c))
    maintenance_target = min(target_c, floor_c + max(0.1, float(maintenance_band_c)))
    stop_margin_c = max(0.0, float(stop_margin_c))

    candidate = find_next_swim_window(
        forecast,
        now,
        score_min=score_min,
        min_air_c=min_air_c,
        swim_time=swim_time,
    )

    base = {
        "should_heat": False,
        "heat_target_c": None,
        "floor_c": round(floor_c, 2),
        "candidate": candidate,
        "required_hours": 0.0,
        "planned_start": None,
        "reason": "",
    }

    # No credible bathing day in the forecast: preserve recoverability only.
    if candidate is None:
        if water_c >= maintenance_target - stop_margin_c:
            base["reason"] = (
                f"aucune fenêtre baignade, maintien plancher {floor_c:.1f} °C"
            )
            return base

        if water_c < floor_c and _inside_preferred_window(
            now, heating_window_start, heating_window_end
        ):
            base.update(
                should_heat=True,
                heat_target_c=round(maintenance_target, 2),
                reason=(
                    f"aucune fenêtre baignade, recharge plancher vers "
                    f"{maintenance_target:.1f} °C"
                ),
            )
            return base

        base["reason"] = (
            f"aucune fenêtre baignade, attendre heures favorables "
            f"(plancher {floor_c:.1f} °C)"
        )
        return base

    deficit = max(0.0, target_c - water_c)
    required_hours = deficit / heating_rate
    margin = float(last_chance_margin_h if candidate["last_chance"] else safety_margin_h)
    total_required = required_hours + max(0.0, margin)
    planned_start = latest_preferred_start(
        candidate["target_datetime"],
        total_required,
        window_start=heating_window_start,
        window_end=heating_window_end,
    )

    base["required_hours"] = round(required_hours, 2)
    base["planned_start"] = planned_start

    if water_c >= target_c - stop_margin_c:
        base["reason"] = (
            f"température prête pour J+{candidate['index']} "
            f"(score {candidate['score']:.0f})"
        )
        return base

    # A very low pool is allowed to recover during daytime even when the bathing
    # deadline is still far away. This protects recoverability without 24/7 heat.
    if water_c < floor_c and _inside_preferred_window(
        now, heating_window_start, heating_window_end
    ) and now < planned_start:
        base.update(
            should_heat=True,
            heat_target_c=round(maintenance_target, 2),
            reason=(
                f"préservation avant J+{candidate['index']}, recharge plancher "
                f"vers {maintenance_target:.1f} °C"
            ),
        )
        return base

    if now >= planned_start:
        chance = " dernière occasion probable" if candidate["last_chance"] else ""
        base.update(
            should_heat=True,
            heat_target_c=round(target_c, 2),
            reason=(
                f"préparer J+{candidate['index']} score {candidate['score']:.0f};"
                f"{chance} besoin ~{required_hours:.1f} h"
            ).strip(),
        )
        return base

    base["reason"] = (
        f"attente avant J+{candidate['index']} score {candidate['score']:.0f}; "
        f"démarrage prévu {planned_start.strftime('%a %H:%M')}"
    )
    return base
