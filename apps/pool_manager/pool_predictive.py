# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Pure helpers for season-wide predictive pool heating."""

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


def _combine(day, value, tzinfo=None, default="16:00:00"):
    result = datetime.datetime.combine(day, _parse_time(value, default))
    if tzinfo is not None:
        result = result.replace(tzinfo=tzinfo)
    return result


def swim_day_score(
    entry,
    min_air_c=21.0,
    ideal_air_c=26.0,
    wind_penalty_from=15.0,
):
    """Return a 0..100 bathing-opportunity score.

    Temperature and sunshine dominate. Rain probability/amount and strong wind
    reduce the score. Missing optional fields are ignored instead of guessed.
    """
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


def heating_day_quality(entry):
    """Return a 0..100 score for favorable daytime PAC/PV heating."""
    temperature = _number(entry.get("temperature"))
    if temperature is None:
        temp_factor = 0.5
    else:
        temp_factor = _clamp((temperature - 5.0) / 25.0, 0.0, 1.0)

    condition = str(entry.get("condition") or "").strip().lower()
    sun_factor = _CONDITION_SUN_FACTOR.get(condition, 0.45)
    cloud = _number(entry.get("cloud_coverage"))
    if cloud is not None:
        cloud_factor = 1.0 - _clamp(cloud, 0.0, 100.0) / 100.0
        sun_factor = 0.70 * sun_factor + 0.30 * cloud_factor

    score = 60.0 * temp_factor + 40.0 * _clamp(sun_factor, 0.0, 1.0)
    return round(_clamp(score, 0.0, 100.0), 1)


def forecast_horizon_profile(index):
    """Return confidence/weight for the 15-day strategic outlook."""
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
    """Normalize Home Assistant daily forecast entries up to 15 days.

    Raw weather quality remains visible as ``score``. ``strategic_score``
    applies a distance-confidence weight so far forecasts can inform the
    outlook without being treated as equally certain as J0-J3.
    """
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
        item["heating_quality"] = heating_day_quality(item)
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
    now,
    score_min=55.0,
    min_air_c=21.0,
    swim_time="16:00:00",
):
    """Return credible bathing opportunities using distance weighting."""
    score_min = float(score_min)
    min_air_c = float(min_air_c)
    opportunities = []

    for index, day in enumerate(forecast or []):
        date_value = day.get("date")
        if date_value is None:
            date_value = now.date() + datetime.timedelta(days=index)
        swim_datetime = _combine(
            date_value,
            swim_time,
            now.tzinfo,
            default="16:00:00",
        )
        temperature = _number(day.get("temperature"))
        raw_score = _number(day.get("score")) or 0.0
        profile = forecast_horizon_profile(index)
        strategic_score = _number(day.get("strategic_score"))
        if strategic_score is None:
            strategic_score = raw_score * profile["weight"]
        confidence = day.get("confidence") or profile["confidence"]
        horizon_weight = _number(day.get("horizon_weight"))
        if horizon_weight is None:
            horizon_weight = profile["weight"]

        if swim_datetime <= now:
            continue
        if (
            temperature is None
            or temperature < min_air_c
            or strategic_score < score_min
        ):
            continue

        bad_streak = 0
        for relative, following in enumerate(
            (forecast or [])[index + 1 : index + 4], start=1
        ):
            following_index = index + relative
            f_temp = _number(following.get("temperature"))
            f_raw_score = _number(following.get("score")) or 0.0
            f_profile = forecast_horizon_profile(following_index)
            f_strategic_score = _number(following.get("strategic_score"))
            if f_strategic_score is None:
                f_strategic_score = f_raw_score * f_profile["weight"]
            if (
                f_temp is not None
                and f_temp >= min_air_c
                and f_strategic_score >= score_min
            ):
                break
            bad_streak += 1

        opportunities.append(
            {
                "index": index,
                "date": date_value,
                "swim_datetime": swim_datetime,
                "temperature": temperature,
                "score": raw_score,
                "strategic_score": round(float(strategic_score), 1),
                "horizon_weight": round(float(horizon_weight), 2),
                "confidence": confidence,
                "condition": day.get("condition"),
                "bad_streak_after": bad_streak,
                "last_chance": bad_streak >= 2,
            }
        )

    return opportunities


def ambient_bin(temperature):
    """Return a stable learning bucket for PAC heating performance."""
    value = _number(temperature)
    if value is None:
        return "unknown"
    if value < 10.0:
        return "lt10"
    if value < 15.0:
        return "10_15"
    if value < 20.0:
        return "15_20"
    if value < 25.0:
        return "20_25"
    return "ge25"


def update_heating_rate_model(model, ambient_c, sample_rate_c_per_h, alpha=0.25):
    """Update one EWMA heating-rate bucket and return a copied model."""
    rate = _number(sample_rate_c_per_h)
    if rate is None or not (0.05 <= rate <= 1.20):
        return dict(model or {})

    alpha = _clamp(float(alpha), 0.05, 1.0)
    key = ambient_bin(ambient_c)
    result = dict(model or {})
    current = dict(result.get(key) or {})
    previous = _number(current.get("rate"))
    count = int(current.get("count") or 0)
    learned = rate if previous is None else previous + alpha * (rate - previous)
    result[key] = {
        "rate": round(learned, 4),
        "count": count + 1,
    }
    return result


def estimate_heating_rate(base_rate_c_per_h, ambient_c=None, learned_model=None):
    """Estimate PAC water-heating rate from weather and optional learned data."""
    base = max(0.05, float(base_rate_c_per_h))
    ambient = _number(ambient_c)

    if ambient is None:
        weather_adjusted = base
    else:
        factor = _clamp(1.0 + 0.018 * (ambient - 20.0), 0.65, 1.25)
        weather_adjusted = base * factor

    key = ambient_bin(ambient)
    learned = (learned_model or {}).get(key)
    learned_rate = _number((learned or {}).get("rate"))
    learned_count = int((learned or {}).get("count") or 0)
    if learned_rate is not None and learned_count > 0:
        weight = min(0.80, 0.35 + 0.10 * learned_count)
        return round(
            weight * learned_rate + (1.0 - weight) * weather_adjusted,
            4,
        )

    return round(weather_adjusted, 4)


def _window(day, start_value, end_value, tzinfo, end_default="20:00:00"):
    start = _combine(day, start_value, tzinfo, default="12:00:00")
    end = _combine(day, end_value, tzinfo, default=end_default)
    if end <= start:
        end += datetime.timedelta(days=1)
    return start, end


def _clip_window(start, end, now, deadline):
    start = max(start, now)
    end = min(end, deadline)
    if end <= start:
        return None
    return start, end


def _allocate_latest(hours, start, end, kind):
    hours = max(0.0, float(hours))
    capacity = max(0.0, (end - start).total_seconds() / 3600.0)
    used = min(hours, capacity)
    if used <= 0.0:
        return None, hours
    segment = {
        "start": end - datetime.timedelta(hours=used),
        "end": end,
        "hours": round(used, 3),
        "kind": kind,
    }
    return segment, max(0.0, hours - used)


def build_heating_schedule(
    *,
    now,
    ready_datetime,
    swim_datetime,
    required_hours,
    previous_day_start="12:00:00",
    previous_day_end="20:00:00",
    morning_start="07:00:00",
    forecast=None,
):
    """Allocate heating with the previous day as the normal first choice.

    The previous afternoon/evening is used first even for a small heat demand.
    The target-day morning is a top-up window. Earlier days are used only when
    the required hours do not fit in those two windows. If the plan is already
    late, an emergency segment starts immediately so comfort is recovered rather
    than silently missing the bathing window.
    """
    required = max(0.0, float(required_hours))
    if required <= 0.0:
        return []

    tzinfo = ready_datetime.tzinfo
    deadline = ready_datetime if now < ready_datetime else swim_datetime
    if deadline <= now:
        return []

    target_day = ready_datetime.date()
    previous_day = target_day - datetime.timedelta(days=1)
    segments = []
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
        previous_day,
        previous_day_start,
        previous_day_end,
        tzinfo,
    )
    clipped = _clip_window(prev_start, prev_end, now, deadline)
    if clipped is not None:
        segment, remaining = _allocate_latest(
            remaining, clipped[0], clipped[1], "previous_day"
        )
        if segment:
            segments.append(segment)

    morning_begin = _combine(
        target_day,
        morning_start,
        tzinfo,
        default="07:00:00",
    )
    morning_end = min(ready_datetime, deadline)
    clipped = _clip_window(morning_begin, morning_end, now, deadline)
    if remaining > 0.0 and clipped is not None:
        segment, remaining = _allocate_latest(
            remaining, clipped[0], clipped[1], "morning_topup"
        )
        if segment:
            segments.append(segment)

    day = previous_day - datetime.timedelta(days=1)
    for _ in range(9):
        if remaining <= 0.0 or day < now.date():
            break
        early_start, early_end = _window(
            day,
            previous_day_start,
            previous_day_end,
            tzinfo,
        )
        # Do not overlap a weather-aware preload already reserved on the
        # same earlier day. Fill the free part immediately before that preload.
        same_day_starts = [
            item["start"]
            for item in segments
            if item["start"].date() == day
        ]
        if same_day_starts:
            early_end = min(early_end, min(same_day_starts))

        clipped = _clip_window(early_start, early_end, now, deadline)
        if clipped is not None:
            segment, remaining = _allocate_latest(
                remaining, clipped[0], clipped[1], "early_preheat"
            )
            if segment:
                segments.append(segment)
        day -= datetime.timedelta(days=1)

    if remaining > 0.0:
        future_starts = sorted(s["start"] for s in segments if s["start"] > now)
        emergency_end = future_starts[0] if future_starts else deadline
        emergency_capacity = max(
            0.0,
            (emergency_end - now).total_seconds() / 3600.0,
        )
        emergency_used = min(remaining, emergency_capacity)
        if emergency_used > 0.0:
            segments.append(
                {
                    "start": now,
                    "end": now + datetime.timedelta(hours=emergency_used),
                    "hours": round(emergency_used, 3),
                    "kind": "emergency",
                }
            )

    segments.sort(key=lambda item: item["start"])
    return segments


def _inside_window(now, start="12:00:00", end="20:00:00"):
    start_time = _parse_time(start, "12:00:00")
    end_time = _parse_time(end, "20:00:00")
    current = now.timetz().replace(tzinfo=None)
    if start_time <= end_time:
        return start_time <= current < end_time
    return current >= start_time or current < end_time


def build_predictive_plan(
    *,
    now,
    water_c,
    target_c,
    forecast,
    heating_rate_c_per_h,
    floor_delta_c,
    score_min=55.0,
    min_air_c=21.0,
    swim_time="16:00:00",
    ready_time="11:00:00",
    maintenance_band_c=0.5,
    stop_margin_c=0.2,
    safety_margin_h=0.5,
    last_chance_margin_h=1.0,
    previous_day_start="12:00:00",
    previous_day_end="20:00:00",
    morning_start="07:00:00",
    operational_horizon_days=3,
):
    """Build one season-wide predictive heating decision."""
    water_c = float(water_c)
    target_c = float(target_c)
    heating_rate = max(0.05, float(heating_rate_c_per_h))
    floor_c = target_c - max(0.0, float(floor_delta_c))
    maintenance_target = min(
        target_c,
        floor_c + max(0.1, float(maintenance_band_c)),
    )
    stop_margin_c = max(0.0, float(stop_margin_c))
    operational_horizon_days = max(1, min(10, int(operational_horizon_days)))

    opportunities = find_swim_opportunities(
        forecast,
        now,
        score_min=score_min,
        min_air_c=min_air_c,
        swim_time=swim_time,
    )
    candidate = opportunities[0] if opportunities else None

    base = {
        "should_heat": False,
        "heat_target_c": None,
        "floor_c": round(floor_c, 2),
        "maintenance_target_c": round(maintenance_target, 2),
        "candidate": candidate,
        "opportunities": opportunities,
        "required_hours": 0.0,
        "scheduled_hours": 0.0,
        "schedule": [],
        "active_segment": None,
        "next_segment": None,
        "ready_datetime": None,
        "operational_horizon_days": operational_horizon_days,
        "reason": "",
    }

    if candidate is None:
        if water_c >= maintenance_target - stop_margin_c:
            base["reason"] = (
                f"aucune baignade probable, réserve {floor_c:.1f} °C"
            )
            return base

        if water_c < floor_c and _inside_window(
            now,
            previous_day_start,
            previous_day_end,
        ):
            base.update(
                should_heat=True,
                heat_target_c=round(maintenance_target, 2),
                reason=(
                    f"mauvais temps prolongé, recharge réserve vers "
                    f"{maintenance_target:.1f} °C"
                ),
            )
            return base

        base["reason"] = (
            f"aucune baignade probable, attente (réserve {floor_c:.1f} °C)"
        )
        return base

    ready_datetime = _combine(
        candidate["date"],
        ready_time,
        now.tzinfo,
        default="11:00:00",
    )
    candidate["ready_datetime"] = ready_datetime
    base["ready_datetime"] = ready_datetime

    candidate_index = int(candidate.get("index") or 0)
    if candidate_index > operational_horizon_days:
        confidence = (
            candidate.get("confidence")
            or forecast_horizon_profile(candidate_index)["confidence"]
        )
        if water_c < floor_c and _inside_window(
            now,
            previous_day_start,
            previous_day_end,
        ):
            base.update(
                should_heat=True,
                heat_target_c=round(maintenance_target, 2),
                reason=(
                    f"tendance {confidence} J+{candidate_index}; "
                    f"recharge réserve vers {maintenance_target:.1f} °C"
                ),
            )
        else:
            base["reason"] = (
                f"tendance {confidence} J+{candidate_index}; observation 15 j, "
                f"planification opérationnelle à J+{operational_horizon_days}"
            )
        return base

    deficit = max(0.0, target_c - water_c)
    required_hours = deficit / heating_rate
    margin = (
        float(last_chance_margin_h)
        if candidate["last_chance"]
        else float(safety_margin_h)
    )
    total_hours = required_hours + max(0.0, margin)

    schedule = build_heating_schedule(
        now=now,
        ready_datetime=ready_datetime,
        swim_datetime=candidate["swim_datetime"],
        required_hours=total_hours,
        previous_day_start=previous_day_start,
        previous_day_end=previous_day_end,
        morning_start=morning_start,
        forecast=forecast,
    )
    base["required_hours"] = round(required_hours, 2)
    base["scheduled_hours"] = round(
        sum(float(item.get("hours") or 0.0) for item in schedule),
        2,
    )
    base["schedule"] = schedule

    active = None
    next_segment = None
    for item in schedule:
        if item["start"] <= now < item["end"]:
            active = item
            break
        if item["start"] > now and next_segment is None:
            next_segment = item
    base["active_segment"] = active
    base["next_segment"] = next_segment

    if water_c >= target_c - stop_margin_c:
        base["reason"] = (
            f"eau prête pour J+{candidate['index']} "
            f"(score {candidate['score']:.0f})"
        )
        return base

    if (
        water_c < floor_c
        and active is None
        and next_segment is not None
        and _inside_window(now, previous_day_start, previous_day_end)
    ):
        base.update(
            should_heat=True,
            heat_target_c=round(maintenance_target, 2),
            reason=(
                f"préservation avant J+{candidate['index']}, réserve vers "
                f"{maintenance_target:.1f} °C"
            ),
        )
        return base

    if active is not None:
        label = "urgence" if active.get("kind") == "emergency" else "planning"
        chance = " dernière occasion probable" if candidate["last_chance"] else ""
        base.update(
            should_heat=True,
            heat_target_c=round(target_c, 2),
            reason=(
                f"{label} J+{candidate['index']} score {candidate['score']:.0f};"
                f"{chance} besoin ~{required_hours:.1f} h"
            ).strip(),
        )
        return base

    if next_segment is not None:
        base["reason"] = (
            f"baignade J+{candidate['index']} score {candidate['score']:.0f}; "
            f"chauffe {next_segment['start'].strftime('%a %H:%M')}"
        )
        return base

    if now < candidate["swim_datetime"]:
        base.update(
            should_heat=True,
            heat_target_c=round(target_c, 2),
            reason=(
                f"rattrapage immédiat J+{candidate['index']} "
                f"score {candidate['score']:.0f}"
            ),
        )
        return base

    base["reason"] = "aucun créneau de chauffe utile"
    return base


def dashboard_forecast(forecast, plan, now):
    """Return compact JSON-serializable rows for a Mushroom/dashboard card."""
    candidate = (plan or {}).get("candidate") or {}
    selected_date = candidate.get("date")
    schedule = (plan or {}).get("schedule") or []
    rows = []

    for index, day in enumerate(forecast or []):
        day_date = day.get("date")
        if day_date is None:
            day_date = now.date() + datetime.timedelta(days=index)
        slots = []
        for item in schedule:
            if item["start"].date() == day_date:
                slots.append(
                    {
                        "start": item["start"].strftime("%H:%M"),
                        "end": item["end"].strftime("%H:%M"),
                        "kind": item.get("kind"),
                    }
                )
        rows.append(
            {
                "offset": index,
                "label": "J0" if index == 0 else f"J+{index}",
                "date": day_date.isoformat(),
                "temperature": _number(day.get("temperature")),
                "condition": day.get("condition"),
                "precipitation_probability": _number(
                    day.get("precipitation_probability")
                ),
                "wind_speed": _number(day.get("wind_speed")),
                "score": _number(day.get("score")) or 0.0,
                "strategic_score": _number(day.get("strategic_score")) or 0.0,
                "horizon_weight": _number(day.get("horizon_weight")) or 1.0,
                "confidence": day.get("confidence") or forecast_horizon_profile(index)["confidence"],
                "operational": index <= int((plan or {}).get("operational_horizon_days") or 3),
                "heating_quality": _number(day.get("heating_quality")) or 0.0,
                "swim": day_date == selected_date,
                "heating": bool(slots),
                "heating_slots": slots,
            }
        )
    return rows
