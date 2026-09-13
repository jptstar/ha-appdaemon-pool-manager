from pathlib import Path


planner = Path("apps/pool_manager/pool_predictive.py")
text = planner.read_text(encoding="utf-8")
old = '''        clipped = _clip_window(early_start, early_end, now, deadline)
        if clipped is not None:
            segment, remaining = _allocate_latest(
                remaining, clipped[0], clipped[1], "early_preheat"
            )
            if segment:
                segments.append(segment)
'''
new = '''        # Do not overlap a weather-aware preload already reserved on the
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
'''
if text.count(old) != 1:
    raise RuntimeError(f"planner patch expected 1 match, got {text.count(old)}")
planner.write_text(text.replace(old, new, 1), encoding="utf-8")


tests = Path("tests/test_predictive_heating.py")
test_text = tests.read_text(encoding="utf-8")
if "test_large_plan_never_overlaps_weather_preload" not in test_text:
    test_text += '''\n\ndef test_large_plan_never_overlaps_weather_preload():
    now = datetime.datetime(2026, 9, 13, 10, 0)
    forecast = _forecast(
        now,
        [
            (20, "cloudy", 40, 8),
            (28, "sunny", 0, 5),
            (14, "rainy", 95, 20),
            (27, "sunny", 0, 5),
        ],
    )
    ready = datetime.datetime(2026, 9, 16, 11, 0)
    swim = datetime.datetime(2026, 9, 16, 16, 0)
    slots = module.build_heating_schedule(
        now=now,
        ready_datetime=ready,
        swim_datetime=swim,
        required_hours=15.0,
        forecast=forecast,
    )
    ordered = sorted(slots, key=lambda item: item["start"])
    for previous, current in zip(ordered, ordered[1:]):
        assert previous["end"] <= current["start"]
'''
    tests.write_text(test_text, encoding="utf-8")

print("schedule overlap fix applied")
