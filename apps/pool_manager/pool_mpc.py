# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Adaptive thermal model + model-predictive control for Pool Manager.

The controller stays deterministic and safety-agnostic: hydraulic/freeze/forced
stop protections remain in the surrounding runtime.  This module only answers
which thermal trajectory is cheapest while keeping the selected bathing window
reachable.

The adaptive model reuses the certified-learning data already collected by
Pool Manager.  MPC then simulates future OFF/Smart/Turbo choices and minimizes
predicted PAC energy.  The optimization is repeated on every control cycle, so
a new certified temperature or weather forecast immediately changes the plan.
"""

import datetime
import math

from pool_predictive import (
    ambient_bin,
    build_predictive_plan,
    estimate_heating_rate,
    estimate_loss_rate,
    find_swim_opportunities,
    normalize_preset_name,
)


def _number(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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
    value = _number((item or {}).get("templow"))
    if value is not None:
        return value
    day = _day_temperature(item)
    return None if day is None else day - 5.0


def _hours_options(limit_h, step_h):
    limit = max(0.0, float(limit_h))
    step = max(0.25, float(step_h))
    if limit <= 0:
        return [0.0]
    values = [0.0]
    cursor = step
    while cursor < limit - 0.05:
        values.append(round(cursor, 3))
        cursor += step
    if not values or abs(values[-1] - limit) > 0.05:
        values.append(round(limit, 3))
    return values


class AdaptiveThermalModel:
    """Thin predictive model around persistent certified installation learning."""

    def __init__(
        self,
        *,
        base_heating_rate_c_per_h,
        heating_rate_model=None,
        loss_model=None,
        cover_state="closed",
        loss_fallback_delta10_c_per_h=0.05,
        smart_preset="Smart",
        turbo_preset="Turbo",
        smart_power_fallback_w=1200.0,
        turbo_power_fallback_w=1900.0,
    ):
        self.base_rate = max(0.05, float(base_heating_rate_c_per_h))
        self.heating_model = heating_rate_model or {}
        self.loss_model = loss_model or {}
        self.cover_state = cover_state
        self.loss_fallback = max(0.0, float(loss_fallback_delta10_c_per_h))
        self.smart_preset = smart_preset
        self.turbo_preset = turbo_preset
        self.smart_power_fallback_w = max(100.0, float(smart_power_fallback_w))
        self.turbo_power_fallback_w = max(
            self.smart_power_fallback_w,
            float(turbo_power_fallback_w),
        )

    def heating_rate(self, preset, ambient_c):
        return estimate_heating_rate(
            self.base_rate,
            preset,
            ambient_c,
            learned_model=self.heating_model,
        ) * getattr(self, "heating_safety_factor", 1.0)

    def heating_power_w(self, preset, ambient_c):
        key = normalize_preset_name(preset)
        fallback = (
            self.turbo_power_fallback_w
            if key == normalize_preset_name(self.turbo_preset)
            and key != normalize_preset_name(self.smart_preset)
            else self.smart_power_fallback_w
        )
        learned = ((self.heating_model.get(key) or {}).get(ambient_bin(ambient_c)) or {})
        power = _number(learned.get("power_w"))
        count = int(learned.get("count") or 0)
        if power is None or count <= 0:
            return round(fallback, 1)
        weight = min(0.90, 0.35 + 0.10 * count)
        return round(weight * power + (1.0 - weight) * fallback, 1)

    def loss_rate(self, water_c, ambient_c):
        return estimate_loss_rate(
            water_c,
            ambient_c,
            cover_state=self.cover_state,
            learned_model=self.loss_model,
            fallback_delta10_c_per_h=self.loss_fallback,
        )

    def confidence(self):
        heat_counts = []
        for bins in self.heating_model.values():
            for entry in (bins or {}).values():
                heat_counts.append(int((entry or {}).get("count") or 0))
        loss_counts = []
        for bins in self.loss_model.values():
            for entry in (bins or {}).values():
                loss_counts.append(int((entry or {}).get("count") or 0))

        heat_samples = sum(heat_counts)
        loss_samples = sum(loss_counts)
        effective = min(heat_samples, loss_samples)
        if effective <= 0:
            level = "cold_start"
        elif effective < 4:
            level = "learning"
        elif effective < 12:
            level = "adapted"
        else:
            level = "mature"
        return {
            "level": level,
            "heating_samples": heat_samples,
            "loss_samples": loss_samples,
        }


def _simulate_day(
    *,
    model,
    start_c,
    day_air_c,
    night_air_c,
    day_window_h,
    night_window_h,
    heat_hours,
    preset,
    candidate_day,
):
    """Simulate one MPC day.

    Learned PAC rates are net rates while heating is active, so passive loss is
    applied only to non-heating hours.  Candidate-day temperature is evaluated
    at the usage window and therefore excludes the following night.
    """
    start = float(start_c)
    day_window = max(0.0, float(day_window_h))
    night_window = max(0.0, float(night_window_h))
    total_heat = max(0.0, float(heat_hours))

    day_heat = min(day_window, total_heat)
    night_heat = 0.0 if candidate_day else min(
        night_window,
        max(0.0, total_heat - day_heat),
    )

    off_day_h = max(0.0, day_window - day_heat)
    day_loss = model.loss_rate(start, day_air_c) * off_day_h
    after_passive_day = start - day_loss

    day_rate = 0.0
    day_power = 0.0
    if day_heat > 0:
        day_rate = model.heating_rate(preset, day_air_c)
        day_power = model.heating_power_w(preset, day_air_c)
    after_day = after_passive_day + day_rate * day_heat
    energy_kwh = day_power * day_heat / 1000.0

    night_loss = 0.0
    night_rate = 0.0
    night_power = 0.0
    after_night = after_day
    if not candidate_day:
        off_night_h = max(0.0, night_window - night_heat)
        night_loss = model.loss_rate(after_day, night_air_c) * off_night_h
        after_night = after_day - night_loss
        if night_heat > 0:
            night_rate = model.heating_rate(preset, night_air_c)
            night_power = model.heating_power_w(preset, night_air_c)
            after_night += night_rate * night_heat
            energy_kwh += night_power * night_heat / 1000.0

    return {
        "start_c": start,
        "day_end_c": after_day,
        "end_c": after_day if candidate_day else after_night,
        "day_heat_h": day_heat,
        "night_heat_h": night_heat,
        "day_loss_c": day_loss,
        "night_loss_c": night_loss,
        "day_rate_c_per_h": day_rate,
        "night_rate_c_per_h": night_rate,
        "energy_kwh": energy_kwh,
    }


def _optimize_candidate(
    *,
    now,
    water_c,
    target_c,
    candidate,
    forecast,
    model,
    floor_c,
    stop_margin_c,
    day_hours,
    today_day_hours_remaining,
    candidate_day_hours,
    night_hours,
    step_h,
    state_step_c,
    turbo_penalty_kwh_per_h,
    night_penalty_kwh_per_h,
    allow_night,
):
    today = now.date() if isinstance(now, datetime.datetime) else now
    end_date = candidate["date"]
    by_date = _forecast_by_date(forecast)
    state_step = max(0.05, float(state_step_c))
    stop_margin = max(0.0, float(stop_margin_c))

    days = []
    cursor = today
    while cursor <= end_date:
        days.append(cursor)
        cursor += datetime.timedelta(days=1)

    states = {
        round(float(water_c) / state_step) * state_step: {
            "temp": float(water_c),
            "cost": 0.0,
            "energy_kwh": 0.0,
            "night_heat_h": 0.0,
            "path": [],
        }
    }

    for index, date_value in enumerate(days):
        candidate_day = date_value == end_date
        if index == 0:
            available_day_h = max(0.0, float(today_day_hours_remaining))
        elif candidate_day:
            available_day_h = max(0.0, float(candidate_day_hours))
        else:
            available_day_h = max(0.0, float(day_hours))

        available_night_h = 0.0 if candidate_day or not allow_night else max(
            0.0, float(night_hours)
        )
        total_limit_h = available_day_h + available_night_h
        hour_options = _hours_options(total_limit_h, step_h)

        actions = [(None, 0.0)]
        for hours in hour_options:
            if hours <= 0:
                continue
            actions.append((model.smart_preset, hours))
            actions.append((model.turbo_preset, hours))

        entry = by_date.get(date_value) or {}
        day_air = _day_temperature(entry)
        night_air = _night_temperature(entry)

        next_states = {}
        for record in states.values():
            for preset, hours in actions:
                sim = _simulate_day(
                    model=model,
                    start_c=record["temp"],
                    day_air_c=day_air,
                    night_air_c=night_air,
                    day_window_h=available_day_h,
                    night_window_h=available_night_h if allow_night else max(
                        0.0, float(night_hours)
                    ),
                    heat_hours=hours,
                    preset=preset or model.smart_preset,
                    candidate_day=candidate_day,
                )
                end_temp = sim["end_c"]

                # Absolute floor remains a hard user constraint.  MPC may choose
                # any warmer dynamic reserve as long as it can still reach the
                # selected bathing target.
                if not candidate_day and end_temp < float(floor_c) - stop_margin:
                    continue

                # Avoid paying for deliberate overheating beyond a small search
                # margin; the runtime will re-optimize again before that point.
                if end_temp > float(target_c) + 1.0:
                    continue

                energy = record["energy_kwh"] + sim["energy_kwh"]
                cost = record["cost"] + sim["energy_kwh"]
                if preset and normalize_preset_name(preset) == normalize_preset_name(
                    model.turbo_preset
                ):
                    cost += (
                        sim["day_heat_h"] + sim["night_heat_h"]
                    ) * max(0.0, float(turbo_penalty_kwh_per_h))
                cost += sim["night_heat_h"] * max(
                    0.0, float(night_penalty_kwh_per_h)
                )

                path_item = {
                    "date": date_value,
                    "preset": preset,
                    "heat_hours": round(float(hours), 2),
                    "day_heat_hours": round(sim["day_heat_h"], 2),
                    "night_heat_hours": round(sim["night_heat_h"], 2),
                    "start_temperature": round(sim["start_c"], 2),
                    "day_end_temperature": round(sim["day_end_c"], 2),
                    "end_temperature": round(sim["end_c"], 2),
                    "energy_kwh": round(sim["energy_kwh"], 2),
                    "day_air_temperature": day_air,
                    "night_air_temperature": night_air,
                }
                key = round(end_temp / state_step) * state_step
                candidate_record = {
                    "temp": end_temp,
                    "cost": cost,
                    "energy_kwh": energy,
                    "night_heat_h": record["night_heat_h"] + sim["night_heat_h"],
                    "path": record["path"] + [path_item],
                }
                previous = next_states.get(key)
                if previous is None or candidate_record["cost"] < previous["cost"]:
                    next_states[key] = candidate_record

        if not next_states:
            return None

        # Bound computation while keeping a broad temperature frontier.
        if len(next_states) > 260:
            ordered = sorted(
                next_states.items(),
                key=lambda kv: (
                    kv[1]["cost"],
                    abs(float(target_c) - kv[1]["temp"]),
                ),
            )[:260]
            next_states = dict(ordered)
        states = next_states

    feasible = [
        record
        for record in states.values()
        if record["temp"] >= float(target_c) - stop_margin
    ]
    if not feasible:
        return None

    best = min(
        feasible,
        key=lambda record: (
            record["cost"] + max(0.0, record["temp"] - float(target_c)) * 0.5,
            record["night_heat_h"],
            record["energy_kwh"],
        ),
    )
    return best


def _optimize_horizon(
    *,
    now,
    water_c,
    target_c,
    swim_dates,
    forecast,
    model,
    floor_c,
    stop_margin_c,
    day_hours,
    today_day_hours_remaining,
    candidate_day_hours,
    night_hours,
    step_h,
    state_step_c,
    turbo_penalty_kwh_per_h,
    night_penalty_kwh_per_h,
    allow_night,
):
    """Optimize the complete forecast horizon against every selected swim day.

    The controller can deliberately coast through poor-weather gaps, preheat on
    a more efficient warm day, and preserve only the temperature that remains
    necessary to recover the next comfort target. Night heating is an optional
    second-pass escape route and remains penalized.
    """
    today = now.date() if isinstance(now, datetime.datetime) else now
    by_date = _forecast_by_date(forecast)
    dates = sorted(date_value for date_value in by_date if date_value >= today)
    if not dates:
        return None, None

    swim_dates = set(swim_dates or [])
    state_step = max(0.05, float(state_step_c))
    stop_margin = max(0.0, float(stop_margin_c))

    states = {
        round(float(water_c) / state_step) * state_step: {
            "temp": float(water_c),
            "cost": 0.0,
            "energy_kwh": 0.0,
            "night_heat_h": 0.0,
            "path": [],
        }
    }

    for index, date_value in enumerate(dates):
        swim_day = date_value in swim_dates
        if date_value == today:
            available_day_h = max(0.0, float(today_day_hours_remaining))
        elif swim_day:
            available_day_h = max(0.0, float(candidate_day_hours))
        else:
            available_day_h = max(0.0, float(day_hours))

        if swim_day:
            deadline_hours = (by_date.get(date_value) or {}).get('_mpc_swim_hours')
            if deadline_hours is not None:
                available_day_h = min(available_day_h, max(0.0, deadline_hours))

        night_window_h = max(0.0, float(night_hours))
        heat_night_h = night_window_h if allow_night else 0.0
        hour_options = _hours_options(
            available_day_h + heat_night_h,
            step_h,
        )

        actions = [(None, 0.0)]
        for hours in hour_options:
            if hours <= 0:
                continue
            actions.append((model.smart_preset, hours))
            actions.append((model.turbo_preset, hours))

        entry = by_date.get(date_value) or {}
        day_air = _day_temperature(entry)
        night_air = _night_temperature(entry)

        next_states = {}
        for record in states.values():
            for preset, hours in actions:
                sim = _simulate_day(
                    model=model,
                    start_c=record["temp"],
                    day_air_c=day_air,
                    night_air_c=night_air,
                    day_window_h=available_day_h,
                    night_window_h=night_window_h,
                    heat_hours=hours,
                    preset=preset or model.smart_preset,
                    candidate_day=False,
                )

                if (
                    swim_day
                    and sim["day_end_c"] < float(target_c) - stop_margin
                ):
                    continue

                end_temp = sim["end_c"]
                if end_temp < float(floor_c) - stop_margin:
                    continue

                # A small thermal-storage headroom lets a warm/sunny day carry
                # useful heat across a short poor-weather gap. Larger deliberate
                # overheating would only increase losses and is rejected.
                if max(sim["day_end_c"], end_temp) > float(target_c) + 1.0:
                    continue

                energy = record["energy_kwh"] + sim["energy_kwh"]
                cost = record["cost"] + sim["energy_kwh"]
                if preset and normalize_preset_name(preset) == normalize_preset_name(
                    model.turbo_preset
                ):
                    cost += (
                        sim["day_heat_h"] + sim["night_heat_h"]
                    ) * max(0.0, float(turbo_penalty_kwh_per_h))
                cost += sim["night_heat_h"] * max(
                    0.0, float(night_penalty_kwh_per_h)
                )

                path_item = {
                    "date": date_value,
                    "swim": swim_day,
                    "preset": preset,
                    "heat_hours": round(float(hours), 2),
                    "day_heat_hours": round(sim["day_heat_h"], 2),
                    "night_heat_hours": round(sim["night_heat_h"], 2),
                    "day_window_hours": round(available_day_h, 2),
                    "night_window_hours": round(night_window_h, 2),
                    "start_temperature": round(sim["start_c"], 2),
                    "day_end_temperature": round(sim["day_end_c"], 2),
                    "end_temperature": round(sim["end_c"], 2),
                    "energy_kwh": round(sim["energy_kwh"], 2),
                    "day_air_temperature": day_air,
                    "night_air_temperature": night_air,
                }
                key = round(end_temp / state_step) * state_step
                candidate_record = {
                    "temp": end_temp,
                    "cost": cost,
                    "energy_kwh": energy,
                    "night_heat_h": record["night_heat_h"] + sim["night_heat_h"],
                    "path": record["path"] + [path_item],
                }
                previous = next_states.get(key)
                if previous is None or candidate_record["cost"] < previous["cost"]:
                    next_states[key] = candidate_record

        if not next_states:
            return None, date_value

        if len(next_states) > 320:
            ordered = sorted(
                next_states.items(),
                key=lambda kv: (
                    kv[1]["cost"],
                    abs(float(target_c) - kv[1]["temp"]),
                ),
            )[:320]
            next_states = dict(ordered)
        states = next_states

    best = min(
        states.values(),
        key=lambda record: (
            record["cost"],
            record["night_heat_h"],
            record["energy_kwh"],
        ),
    )
    return best, None


def _simulate_fixed_horizon_path(
    *,
    start_c,
    path,
    forecast,
    model,
    floor_c,
    target_c,
    stop_margin_c,
):
    """Replay one fixed horizon schedule from a different starting temperature."""
    if not path:
        return float(start_c)

    by_date = _forecast_by_date(forecast)
    temp = float(start_c)
    stop_margin = max(0.0, float(stop_margin_c))

    for item in path:
        date_value = item["date"]
        entry = by_date.get(date_value) or {}
        sim = _simulate_day(
            model=model,
            start_c=temp,
            day_air_c=_day_temperature(entry),
            night_air_c=_night_temperature(entry),
            day_window_h=float(item.get("day_window_hours") or 0.0),
            night_window_h=float(item.get("night_window_hours") or 0.0),
            heat_hours=float(item.get("heat_hours") or 0.0),
            preset=item.get("preset") or model.smart_preset,
            candidate_day=False,
        )
        if (
            item.get("swim")
            and sim["day_end_c"] < float(target_c) - stop_margin
        ):
            return False
        temp = sim["end_c"]
        if temp < float(floor_c) - stop_margin:
            return False

    return temp


def _minimum_start_for_horizon_path(
    *,
    path,
    target_c,
    floor_c,
    water_c,
    forecast,
    model,
    stop_margin_c,
):
    """Minimum temperature that keeps every future comfort target recoverable."""
    if not path:
        return float(floor_c)

    low = float(floor_c)
    high = max(float(target_c) + 1.0, float(water_c))

    # The selected schedule is known to be feasible from its planned start.
    if _simulate_fixed_horizon_path(
        start_c=high,
        path=path,
        forecast=forecast,
        model=model,
        floor_c=floor_c,
        target_c=target_c,
        stop_margin_c=stop_margin_c,
    ) is False:
        high = max(high, float(water_c) + 2.0)

    for _ in range(18):
        mid = (low + high) / 2.0
        result = _simulate_fixed_horizon_path(
            start_c=mid,
            path=path,
            forecast=forecast,
            model=model,
            floor_c=floor_c,
            target_c=target_c,
            stop_margin_c=stop_margin_c,
        )
        if result is not False:
            high = mid
        else:
            low = mid

    return max(float(floor_c), min(float(target_c) + 1.0, high))


def _annotate_horizon_path(
    *,
    path,
    swim_dates,
    forecast,
    model,
    floor_c,
    target_c,
    stop_margin_c,
):
    """Add future target, purpose and dynamic recoverability floor per day."""
    ordered_swims = sorted(set(swim_dates or []))
    for index, item in enumerate(path):
        date_value = item["date"]
        target_date = next(
            (d for d in ordered_swims if d >= date_value),
            None,
        )
        item["target_date"] = target_date
        item["recoverability_floor"] = round(
            _minimum_start_for_horizon_path(
                path=path[index:],
                target_c=target_c,
                floor_c=floor_c,
                water_c=float(item.get("start_temperature") or floor_c),
                forecast=forecast,
                model=model,
                stop_margin_c=stop_margin_c,
            ),
            2,
        )

        heat_hours = float(item.get("heat_hours") or 0.0)
        if item.get("swim"):
            item["action"] = "MAINTAIN"
            item["purpose"] = "baignade"
        elif heat_hours > 0.0 and target_date is not None:
            item["action"] = "PREHEAT"
            item["purpose"] = f"préparation baignade {target_date.isoformat()}"
        elif target_date is not None:
            item["action"] = "WAIT"
            item["purpose"] = (
                f"attente économique; réserve "
                f"{item['recoverability_floor']:.1f} °C"
            )
        else:
            item["action"] = "WAIT"
            item["purpose"] = "aucun besoin thermique futur dans l'horizon"

    return path


def _simulate_fixed_path(
    *,
    start_c,
    path,
    forecast,
    model,
    floor_c,
    day_hours,
    today_day_hours_remaining,
    candidate_day_hours,
    night_hours,
    stop_margin_c,
):
    if not path:
        return False
    by_date = _forecast_by_date(forecast)
    temp = float(start_c)
    for index, item in enumerate(path):
        date_value = item["date"]
        candidate_day = index == len(path) - 1
        if index == 0:
            day_window = max(0.0, float(today_day_hours_remaining))
        elif candidate_day:
            day_window = max(0.0, float(candidate_day_hours))
        else:
            day_window = max(0.0, float(day_hours))
        entry = by_date.get(date_value) or {}
        sim = _simulate_day(
            model=model,
            start_c=temp,
            day_air_c=_day_temperature(entry),
            night_air_c=_night_temperature(entry),
            day_window_h=day_window,
            night_window_h=max(0.0, float(night_hours)),
            heat_hours=float(item.get("heat_hours") or 0.0),
            preset=item.get("preset") or model.smart_preset,
            candidate_day=candidate_day,
        )
        temp = sim["end_c"]
        if not candidate_day and temp < float(floor_c) - float(stop_margin_c):
            return False
    return temp


def _minimum_start_for_path(
    *,
    path,
    target_c,
    floor_c,
    water_c,
    forecast,
    model,
    day_hours,
    today_day_hours_remaining,
    candidate_day_hours,
    night_hours,
    stop_margin_c,
):
    low = float(floor_c)
    high = max(float(target_c), float(water_c))
    target = float(target_c) - max(0.0, float(stop_margin_c))

    for _ in range(18):
        mid = (low + high) / 2.0
        result = _simulate_fixed_path(
            start_c=mid,
            path=path,
            forecast=forecast,
            model=model,
            floor_c=floor_c,
            day_hours=day_hours,
            today_day_hours_remaining=today_day_hours_remaining,
            candidate_day_hours=candidate_day_hours,
            night_hours=night_hours,
            stop_margin_c=stop_margin_c,
        )
        if result is not False and result >= target:
            high = mid
        else:
            low = mid
    return max(float(floor_c), min(float(target_c), high))


def _project_no_heat(
    *,
    now,
    water_c,
    candidate_date,
    forecast,
    model,
    day_hours,
    today_day_hours_remaining,
    candidate_day_hours,
    night_hours,
):
    today = now.date() if isinstance(now, datetime.datetime) else now
    by_date = _forecast_by_date(forecast)
    temp = float(water_c)
    total_loss = 0.0
    cursor = today
    index = 0
    while cursor <= candidate_date:
        candidate_day = cursor == candidate_date
        day_window = (
            max(0.0, float(today_day_hours_remaining))
            if index == 0
            else (
                max(0.0, float(candidate_day_hours))
                if candidate_day
                else max(0.0, float(day_hours))
            )
        )
        entry = by_date.get(cursor) or {}
        sim = _simulate_day(
            model=model,
            start_c=temp,
            day_air_c=_day_temperature(entry),
            night_air_c=_night_temperature(entry),
            day_window_h=day_window,
            night_window_h=max(0.0, float(night_hours)),
            heat_hours=0.0,
            preset=model.smart_preset,
            candidate_day=candidate_day,
        )
        total_loss += max(0.0, temp - sim["end_c"])
        temp = sim["end_c"]
        cursor += datetime.timedelta(days=1)
        index += 1
    return total_loss, temp


def build_mpc_plan(
    *,
    now,
    water_c,
    target_c,
    forecast,
    base_heating_rate_c_per_h,
    heating_rate_model=None,
    loss_model=None,
    cover_state="closed",
    floor_delta_c=2.0,
    minimum_water_c=None,
    floor_recharge_c=0.5,
    stop_margin_c=0.2,
    score_min=55.0,
    min_air_c=21.0,
    weekend_bonus=10.0,
    smart_preset="Smart",
    turbo_preset="Turbo",
    day_hours=12.0,
    today_day_hours_remaining=12.0,
    candidate_day_hours=6.0,
    night_hours=12.0,
    loss_fallback_delta10_c_per_h=0.05,
    daylight_active=True,
    smart_power_fallback_w=1200.0,
    turbo_power_fallback_w=1900.0,
    step_h=1.0,
    state_step_c=0.2,
    turbo_penalty_kwh_per_h=0.08,
    night_penalty_kwh_per_h=0.35,
    swim_hour=None,
    allow_night_heating=True,
    heating_safety_factor=1.0,
):
    """Build a full-horizon adaptive energy-minimizing thermal plan.

    Every credible bathing opportunity in the available weather horizon is
    considered, not just the first one. The optimizer may let the pool cool
    through poor-weather gaps, preheat on thermally efficient days, and retain
    only the temperature reserve required to make later comfort windows
    recoverable.
    """
    today = now.date() if isinstance(now, datetime.datetime) else now
    water = float(water_c)
    target = float(target_c)
    if minimum_water_c is None:
        floor_c = target - max(0.0, float(floor_delta_c))
    else:
        floor_c = float(minimum_water_c)
    floor_c = min(target, floor_c)

    model = AdaptiveThermalModel(
        base_heating_rate_c_per_h=base_heating_rate_c_per_h,
        heating_rate_model=heating_rate_model,
        loss_model=loss_model,
        cover_state=cover_state,
        loss_fallback_delta10_c_per_h=loss_fallback_delta10_c_per_h,
        smart_preset=smart_preset,
        turbo_preset=turbo_preset,
        smart_power_fallback_w=smart_power_fallback_w,
        turbo_power_fallback_w=turbo_power_fallback_w,
    )
    confidence = model.confidence()
    model.heating_safety_factor = max(0.5, min(1.0, float(heating_safety_factor)))

    opportunities = find_swim_opportunities(
        forecast,
        today,
        score_min=score_min,
        min_air_c=min_air_c,
        weekend_bonus=weekend_bonus,
    )

    # A deadline is a clock time, not a fresh six-hour allowance at every
    # replan. Carry explicit windows in a copy, never mutate weather history.
    if swim_hour is not None and isinstance(now, datetime.datetime):
        deadline = now.replace(hour=int(swim_hour), minute=0, second=0, microsecond=0)
        if now >= deadline:
            opportunities = [o for o in opportunities if o['date'] != today]
        start_hour = max(0.0, 12.0 - float(day_hours) / 2.0)
        forecast = [dict(row) for row in forecast]
        for row in forecast:
            if row.get('date') == today:
                if now >= deadline:
                    row.update(score=0.0, strategic_score=0.0, usage_score=0.0)
                row['_mpc_swim_hours'] = min(
                    max(0.0, float(today_day_hours_remaining)),
                    max(0.0, (deadline - now).total_seconds() / 3600.0),
                    max(0.0, float(swim_hour) - start_hour),
                )
            else:
                row['_mpc_swim_hours'] = min(float(day_hours), max(0.0, float(swim_hour) - start_hour))

    if not opportunities:
        fallback = build_predictive_plan(
            now=now,
            water_c=water,
            target_c=target,
            forecast=forecast,
            base_heating_rate_c_per_h=base_heating_rate_c_per_h,
            heating_rate_model=heating_rate_model,
            loss_model=loss_model,
            cover_state=cover_state,
            floor_delta_c=floor_delta_c,
            minimum_water_c=minimum_water_c,
            floor_recharge_c=floor_recharge_c,
            stop_margin_c=stop_margin_c,
            score_min=score_min,
            min_air_c=min_air_c,
            smart_preset=smart_preset,
            turbo_preset=turbo_preset,
            day_hours=day_hours,
            today_day_hours_remaining=today_day_hours_remaining,
            candidate_day_hours=candidate_day_hours,
            night_hours=night_hours,
            loss_fallback_delta10_c_per_h=loss_fallback_delta10_c_per_h,
            daylight_active=daylight_active,
        )
        fallback.update(
            planner="MPC",
            adaptive_model=True,
            model_confidence=confidence,
            adaptive_floor_c=round(floor_c, 2),
            mpc_energy_kwh=0.0,
            mpc_horizon_energy_kwh=0.0,
            mpc_night_energy_required=False,
            mpc_plan=[],
            swim_dates=[],
            opportunities=[],
        )
        return fallback

    # Optimize all credible comfort windows. If one particular target is
    # physically unreachable even with exceptional night heating, discard only
    # that failed target and preserve the rest of the horizon.
    active_swim_dates = sorted({item["date"] for item in opportunities})
    missed_swim_dates = []
    optimized = None
    used_night = False
    while active_swim_dates:
        optimized, failed_date = _optimize_horizon(
            now=now,
            water_c=water,
            target_c=target,
            swim_dates=active_swim_dates,
            forecast=forecast,
            model=model,
            floor_c=floor_c,
            stop_margin_c=stop_margin_c,
            day_hours=day_hours,
            today_day_hours_remaining=today_day_hours_remaining,
            candidate_day_hours=candidate_day_hours,
            night_hours=night_hours,
            step_h=step_h,
            state_step_c=state_step_c,
            turbo_penalty_kwh_per_h=turbo_penalty_kwh_per_h,
            night_penalty_kwh_per_h=night_penalty_kwh_per_h,
            allow_night=False,
        )
        used_night = False
        if optimized is None and allow_night_heating:
            optimized, night_failed_date = _optimize_horizon(
                now=now,
                water_c=water,
                target_c=target,
                swim_dates=active_swim_dates,
                forecast=forecast,
                model=model,
                floor_c=floor_c,
                stop_margin_c=stop_margin_c,
                day_hours=day_hours,
                today_day_hours_remaining=today_day_hours_remaining,
                candidate_day_hours=candidate_day_hours,
                night_hours=night_hours,
                step_h=step_h,
                state_step_c=state_step_c,
                turbo_penalty_kwh_per_h=turbo_penalty_kwh_per_h,
                night_penalty_kwh_per_h=night_penalty_kwh_per_h,
                allow_night=True,
            )
            used_night = optimized is not None
            if optimized is None:
                failed_date = night_failed_date or failed_date

        if optimized is not None:
            break

        if failed_date in active_swim_dates:
            missed_swim_dates.append(failed_date)
            active_swim_dates.remove(failed_date)
        else:
            break

    if optimized is None or not active_swim_dates:
        fallback = build_predictive_plan(
            now=now,
            water_c=water,
            target_c=target,
            forecast=forecast,
            base_heating_rate_c_per_h=base_heating_rate_c_per_h,
            heating_rate_model=heating_rate_model,
            loss_model=loss_model,
            cover_state=cover_state,
            floor_delta_c=floor_delta_c,
            minimum_water_c=minimum_water_c,
            floor_recharge_c=floor_recharge_c,
            stop_margin_c=stop_margin_c,
            score_min=101.0,
            min_air_c=min_air_c,
            smart_preset=smart_preset,
            turbo_preset=turbo_preset,
            day_hours=day_hours,
            today_day_hours_remaining=today_day_hours_remaining,
            candidate_day_hours=candidate_day_hours,
            night_hours=night_hours,
            loss_fallback_delta10_c_per_h=loss_fallback_delta10_c_per_h,
            daylight_active=daylight_active,
        )
        fallback.update(
            planner="MPC",
            adaptive_model=True,
            model_confidence=confidence,
            adaptive_floor_c=round(floor_c, 2),
            mpc_energy_kwh=0.0,
            mpc_horizon_energy_kwh=0.0,
            mpc_night_energy_required=False,
            mpc_plan=[],
            swim_dates=[],
            opportunities=opportunities,
            missed_swim_dates=missed_swim_dates,
            reason="aucune fenêtre baignade thermiquement atteignable; réserve minimale",
        )
        return fallback

    path = _annotate_horizon_path(
        path=optimized["path"],
        swim_dates=active_swim_dates,
        forecast=forecast,
        model=model,
        floor_c=floor_c,
        target_c=target,
        stop_margin_c=stop_margin_c,
    )
    first = path[0]

    # Primary candidate remains the next chronological comfort window; all later
    # targets stay visible and constrained in the same MPC plan.
    first_swim_date = min(active_swim_dates)
    selected_candidate = next(
        (
            item
            for item in opportunities
            if item.get("date") == first_swim_date
        ),
        None,
    )
    if selected_candidate is None:
        selected_candidate = {"date": first_swim_date}

    recovery_start = next(
        (
            item["date"]
            for item in path
            if item["date"] <= first_swim_date
            and float(item.get("heat_hours") or 0.0) > 0.0
        ),
        first_swim_date,
    )

    no_heat_loss, projected_no_heat = _project_no_heat(
        now=now,
        water_c=water,
        candidate_date=first_swim_date,
        forecast=forecast,
        model=model,
        day_hours=day_hours,
        today_day_hours_remaining=today_day_hours_remaining,
        candidate_day_hours=candidate_day_hours,
        night_hours=night_hours,
    )

    adaptive_floor = float(first.get("recoverability_floor") or floor_c)
    heat_today_h = float(first.get("day_heat_hours") or 0.0)
    night_today_h = float(first.get("night_heat_hours") or 0.0)
    current_heat_h = heat_today_h if daylight_active else night_today_h
    should_heat = first["date"] == today and current_heat_h > 0.0

    if first.get("swim"):
        action = "MAINTAIN"
    elif should_heat:
        action = "PREHEAT"
    else:
        action = "WAIT"

    preset = first.get("preset") or smart_preset
    if not should_heat:
        preset = smart_preset

    if should_heat and daylight_active:
        heat_target = max(
            adaptive_floor,
            min(target + 1.0, float(first.get("day_end_temperature") or target)),
        )
    elif should_heat:
        heat_target = max(
            adaptive_floor,
            min(target + 1.0, float(first.get("end_temperature") or target)),
        )
    else:
        heat_target = None

    required_gain = max(0.0, target - projected_no_heat)
    thermal_margin = water - adaptive_floor
    next_swim_energy = sum(
        float(item.get("energy_kwh") or 0.0)
        for item in path
        if item["date"] <= first_swim_date
    )

    reason = (
        f"MPC horizon {len(path)} j; "
        f"{len(active_swim_dates)} fenêtre(s) baignade; "
        f"{optimized['energy_kwh']:.1f} kWh prévus"
    )
    if should_heat:
        reason += (
            f"; aujourd'hui {current_heat_h:.1f} h {preset} équiv. "
            f"pour {first.get('target_date')}"
        )
    else:
        reason += (
            f"; attente, réserve récupérabilité {adaptive_floor:.1f} °C"
        )
    if used_night:
        reason += "; chauffe nocturne exceptionnelle nécessaire"

    return {
        "planner": "MPC",
        "missed_swim_dates": missed_swim_dates,
        "swim_hour": swim_hour,
        "adaptive_model": True,
        "model_confidence": confidence,
        "action": action,
        "should_heat": should_heat,
        "heat_target_c": round(heat_target, 2) if heat_target is not None else None,
        "preset": preset,
        "night_heating": bool(
            should_heat and not daylight_active and night_today_h > 0
        ),
        "night_required_c": 0.0,
        "floor_c": round(floor_c, 2),
        "floor_target_c": round(
            min(target, floor_c + max(0.1, float(floor_recharge_c))),
            2,
        ),
        "adaptive_floor_c": round(adaptive_floor, 2),
        "candidate": selected_candidate,
        "opportunities": opportunities,
        "swim_dates": active_swim_dates,
        "recovery_start_date": recovery_start,
        "trajectory_target_c": round(
            heat_target if should_heat and heat_target is not None else adaptive_floor,
            2,
        ),
        "required_gain_c": round(required_gain, 2),
        "predicted_loss_c": round(no_heat_loss, 2),
        "projected_without_heat_c": round(projected_no_heat, 2),
        "future_smart_capacity_c": None,
        "thermal_margin_c": round(thermal_margin, 2),
        "mpc_energy_kwh": round(optimized["energy_kwh"], 2),
        "mpc_horizon_energy_kwh": round(optimized["energy_kwh"], 2),
        "mpc_next_swim_energy_kwh": round(next_swim_energy, 2),
        "mpc_night_energy_required": bool(used_night),
        "mpc_plan": path,
        "reason": reason,
    }
