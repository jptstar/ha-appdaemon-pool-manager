"""Reproducible component audit; does not operate Home Assistant.

Runs real planner/policy methods with deterministic inputs. This is not a
physical pool or complete AppDaemon simulation. Every case and check is saved.
"""
import argparse
import datetime as dt
import importlib.util
import itertools
import json
import math
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pool_manager"))
from pool_mpc import build_mpc_plan
from pool_predictive import normalize_daily_forecast, estimate_loss_rate, normalize_cover_state
from pool_safety import decide_hors_gel_continu
from pool_common import TAB_MODE


def helper(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


decision = helper("test_pool_decision")
energy = helper("test_energy_arbitration")
runtime = helper("test_predictive_runtime_learning")

WEATHER = {
    "stable_hot": [(30, "sunny", 20)] * 3,
    "stable_cold": [(14, "cloudy", 5)] * 3,
    "rain": [(23, "rainy", 12)] * 3,
    "storm": [(25, "lightning-rainy", 16)] * 3,
    "hot_cold_hot": [(29, "sunny", 15), (14, "rainy", 5), (29, "sunny", 15)],
    "cold_hot_cold": [(14, "rainy", 5), (29, "sunny", 15), (14, "rainy", 5)],
    "swim_tomorrow": [(19, "cloudy", 10), (28, "sunny", 15), (19, "cloudy", 10)],
    "swim_delayed": [(19, "cloudy", 10), (19, "cloudy", 10), (28, "sunny", 15)],
    "swim_advanced": [(28, "sunny", 15), (19, "cloudy", 10), (19, "cloudy", 10)],
    "borderline_above": [(23, "partlycloudy", 9)] * 3,
    "borderline_below": [(22, "partlycloudy", 9)] * 3,
    "missing_today": [(28, "sunny", 15)] * 3,
    "missing_middle": [(28, "sunny", 15)] * 3,
    "empty": [],
}


def forecast(now, name):
    raw = []
    for i, (high, condition, low) in enumerate(WEATHER[name]):
        if (name == "missing_today" and i == 0) or (name == "missing_middle" and i == 1):
            continue
        raw.append({"datetime": (now + dt.timedelta(days=i)).isoformat(),
                    "temperature": high, "templow": low, "condition": condition,
                    "wind_speed": 40 if condition == "lightning-rainy" else 4,
                    "precipitation_probability": 90 if "rain" in condition else 0,
                    "precipitation": 15 if "rain" in condition else 0})
    return normalize_daily_forecast(raw)


def plan(now, weather, water, cover, night):
    return build_mpc_plan(
        now=now, water_c=water, target_c=31, forecast=forecast(now, weather),
        base_heating_rate_c_per_h=.3, cover_state=cover,
        minimum_water_c=22, floor_delta_c=4, score_min=55,
        day_hours=10, today_day_hours_remaining=min(10,max(0, 18-now.hour)),
        candidate_day_hours=6, night_hours=14,
        daylight_active=8 <= now.hour < 18, allow_night_heating=night,
        swim_hour_weekday=17, swim_hour_weekend=12,
        loss_fallback_delta10_c_per_h=.05, step_h=2, state_step_c=.2,
        heating_safety_factor=.9)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/scenario-audit")
    args = parser.parse_args()
    records = []

    def record(family, inputs, result, checks):
        records.append({"id": f"{family}-{sum(r['family']==family for r in records)+1:05d}",
                        "family": family, "inputs": inputs, "result": result,
                        "checks": checks, "failed": [k for k,v in checks.items() if not v]})

    started = time.monotonic()
    for weather, water, hour, cover, night in itertools.product(
            WEATHER, [18, 22, 26, 29, 30.7, 31, 32], [0, 7, 10, 12, 17, 23],
            ["closed", "open", "opening", "unknown"], [False, True]):
        now = dt.datetime(2026, 10, 3, hour)
        inputs = dict(weather=weather, water=water, hour=hour, cover=cover, allow_night=night)
        try:
            p = plan(now, weather, water, cover, night)
            path = p.get("mpc_plan", [])
            checks = {
                "night_forbidden_respected": night or not any(r.get("night_heat_hours",0)>0 for r in path),
                "energy_finite_nonnegative": math.isfinite(p.get("mpc_energy_kwh",0)) and p.get("mpc_energy_kwh",0)>=0,
                "selected_and_missed_disjoint": not set(p.get("swim_dates",[])) & set(p.get("missed_swim_dates",[])),
                "heat_target_present": not p.get("should_heat") or p.get("heat_target_c") is not None,
                # Floor preservation remains legitimate with missing weather.
                "no_borrowed_weather_day": weather != "missing_today" or not p.get("should_heat") or p.get("action")=="PRESERVE",
                "night_forbidden_including_fallback": night or 8<=hour<18 or not p.get("should_heat"),
                "missing_days_accounted_for": not path or all((b['date']-a['date']).days==1 for a,b in zip(path,path[1:])),
            }
            record("planner", inputs, {k:p.get(k) for k in (
                "action", "should_heat", "preset", "heat_target_c", "swim_dates",
                "missed_swim_dates", "mpc_energy_kwh", "reason")}, checks)
        except Exception as exc:
            record("planner", inputs, {"exception":repr(exc)}, {"no_exception":False})
    print(f"Planner: {len(records)} cases in {time.monotonic()-started:.1f}s", flush=True)

    # User policy at day/night transitions and weather-driven lost objectives.
    for choice, daylight, water, missed, heat, preset, upcoming in itertools.product(
            [None, "eco", "night", "skip"], [False, True], [26,30.7,31,32],
            ["none", "today", "tomorrow"], [False,True], ["Smart","Turbo"], [False,True]):
        now = dt.datetime(2026,10,3,10 if daylight else 23)
        app = decision.App(); app.daylight = daylight
        app._pool_decision_choice = choice; app._pool_decision_day = now.date()
        app._pool_decision_choice_until = now + dt.timedelta(hours=8)
        md = [] if missed=="none" else [now.date()+dt.timedelta(days=missed=="tomorrow")]
        original = {"should_heat":heat, "preset":preset, "heat_target_c":31 if heat else None,
                    "action":"PREHEAT" if heat else "WAIT", "missed_swim_dates":md,
                    "mpc_plan":[{"date":now.date(), "night_heat_hours":3 if upcoming else 0}],
                    "night_heating":not daylight and heat}
        p = app._apply_pool_decision(original, water, 31, now=now)
        checks = {
            "suspend_wins": choice!="skip" or not p.get("should_heat"),
            "night_authorization_below_target": choice!="night" or daylight or water>=30.8 or p.get("should_heat"),
            "no_unapproved_night": daylight or choice=="night" or not p.get("should_heat"),
            "economy_does_not_force_turbo": choice!="eco" or not p.get("should_heat") or p.get("preset")!="Turbo",
            "explicit_choice_published": choice is None or p.get("decision_choice")==choice,
        }
        record("policy", dict(choice=choice, daylight=daylight, water=water, missed=missed,
               heat=heat, preset=preset, upcoming_night=upcoming), p, checks)

    # Real priority arbitration: import/export, quota and physical PAC activity.
    for remaining, target, done, solar, pump, demand, active, grid in itertools.product(
            [0, .5, 4], [0,14], [0,14], [False,True], [False,True],
            [False,True], [False,True], [-2000,0,301,8000]):
        a = energy.FakeStrategy(remaining_h=remaining, in_solar_window=solar)
        handled = a.appliquer_priorite_pac_ou_quota(done,target,pump,demand,active,"turbo",
                     surplus_net=max(0,-grid),reseau_net=grid)
        commanded = a.speed if a.speed is not None else a.started[0] if a.started else None
        checks = {"command_within_bounds": commanded is None or 47<=commanded<=100,
                  "pac_flow_commanded_if_needed": not (demand or active) or pump and not handled or commanded is not None and commanded>=47,
                  "pac_high_import_handled": not (demand or active) or grid<=300 and solar and pump or handled}
        record("energy", dict(remaining=remaining,target=target,done=done,solar=solar,
               pump=pump,demand=demand,active=active,grid=grid),dict(handled=handled,speed=commanded,message=a.messages),checks)

    # A recent clean certification must not be discarded by a brief restart.
    with tempfile.TemporaryDirectory() as tmp:
        for age, restart, mode, active, cover in itertools.product(
                [1,5,30,360,1440], [False,True], ["Fin de saison • Smart","Turbo • 12 h"],
                [False,True], ["closed","open","unknown"]):
            now=dt.datetime.now(); a=runtime._make_runtime(Path(tmp))
            a.chauffage_mode=lambda:mode; a.pac_active=active; a.cover=cover
            a.chauffage_predictif_certified_at=now-dt.timedelta(minutes=age)
            a.chauffage_predictif_certified_water_c=29
            a.last_pompe_on=now-dt.timedelta(seconds=10) if restart else now-dt.timedelta(days=2)
            a.last_pompe_off=now-dt.timedelta(seconds=40) if restart else None
            stops=[]; a._pac_off=lambda *args,**kwargs:stops.append(args) or False
            a._update_certified_measurement(now)
            checks={"explicit_turbo_no_measurement":not mode.startswith("Turbo") or not a.chauffage_predictif_measurement_active,
                    "fresh_certification_reused_after_brief_restart":not (restart and age<=5 and not mode.startswith("Turbo")) or not a.chauffage_predictif_measurement_active}
            record("calibration",dict(age_minutes=age,restart=restart,mode=mode,pac_active=active,cover=cover),
                   dict(measurement_active=a.chauffage_predictif_measurement_active,stop_requested=bool(stops)),checks)

    # Sequential reforecasting: same water, multiple forecast revisions.
    for water, cover, chain in itertools.product([26,29,30.7],["closed","open"],[
        ["swim_tomorrow","swim_delayed","swim_tomorrow"],
        ["borderline_above","borderline_below","borderline_above"],
        ["swim_delayed","swim_advanced","rain"],
        ["stable_hot","empty","stable_hot"],
    ]):
        now=dt.datetime(2026,10,2,10)
        result=[]
        for name in chain:
            p=plan(now,name,water,cover,True)
            result.append(dict(weather=name,heat=p.get("should_heat"),dates=p.get("swim_dates"),reason=p.get("reason")))
        record("forecast_revision",dict(water=water,cover=cover,chain=chain),result,
               {"deterministic_return_to_same_forecast":chain[0]!=chain[-1] or result[0]==result[-1]})

    # Every ordered transition between weather profiles. A changed objective
    # is an observation, not automatically a bug (storms can cancel bathing).
    for old, new, water, cover, hour in itertools.product(
            WEATHER,WEATHER,[26,30.7],["closed","open"],[7,10,16]):
        now=dt.datetime(2026,10,2,hour)
        before=plan(now,old,water,cover,True)
        after=plan(now,new,water,cover,True)
        bd=(before.get('candidate') or {}).get('date')
        ad=(after.get('candidate') or {}).get('date')
        record('weather_transition',dict(old=old,new=new,water=water,cover=cover,hour=hour),
               dict(old_target=bd,new_target=ad,target_changed=bd!=ad,
                    old_heat=before.get('should_heat'),new_heat=after.get('should_heat'),
                    old_preset=before.get('preset'),new_preset=after.get('preset'),
                    unmet_target_removed=bd is not None and bd not in after.get('swim_dates',[]) and water<30.8),
               {'same_forecast_same_decision':old!=new or (bd==ad and before.get('should_heat')==after.get('should_heat'))})

    # The user's central requirement: a selected Saturday must not silently
    # disappear after noon even when the forecast itself has not changed.
    for water, hour, cover in itertools.product([29.7,30.7,31],[12,13,17],["closed","open"]):
        before=plan(dt.datetime(2026,10,3,10),'stable_hot',water,cover,True)
        now=dt.datetime(2026,10,3,hour)
        after=plan(now,'stable_hot',water,cover,True)
        today=now.date()
        selected=today in before.get('swim_dates',[])
        represented=today in after.get('swim_dates',[]) or today in after.get('missed_swim_dates',[])
        record('deadline_continuity',dict(water=water,hour=hour,cover=cover),
               dict(before=before.get('swim_dates'),after=after.get('swim_dates'),missed=after.get('missed_swim_dates')),
               {'selected_target_not_silently_lost':not selected or water>=30.8 or represented})

    # Sudden sensor/context changes and frost/forced-stop priority.
    for mode, temperature, running in itertools.product(TAB_MODE+[None],
            [None,-10,0,.9,1,2,3,3.1,10], [False,True]):
        result=decide_hors_gel_continu(temperature,running,mode,1,3)
        record('freeze',dict(mode=mode,temperature=temperature,running=running),dict(circulate=result),
               {'forced_stop_wins':mode!=TAB_MODE[4] or not result,
                'frost_starts':mode==TAB_MODE[4] or temperature is None or temperature>=1 or result})

    for raw in ['closed','open','opening','closing','unknown','unavailable',None]:
        normalized=normalize_cover_state(raw)
        loss=estimate_loss_rate(30,10,cover_state=raw)
        record('cover',dict(raw=raw),dict(normalized=normalized,loss_c_per_h=loss),
               {'moving_cover_not_certified_closed':raw!='closing' or normalized!='closed'})

    failed=[r for r in records if r['failed']]
    counts={family:sum(r['family']==family for r in records) for family in sorted({r['family'] for r in records})}
    failures={check:sum(check in r['failed'] for r in records) for check in sorted({c for r in failed for c in r['failed']})}
    args.output.mkdir(parents=True,exist_ok=True)
    payload=dict(scope="component audit; not physical or full AppDaemon simulation",version="working-tree",
                 total=len(records),families=counts,failed_cases=len(failed),failed_checks=failures,cases=records)
    (args.output/'cases.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+'\n')
    summary=dict(total=len(records),families=counts,failed_cases=len(failed),failed_checks=failures,
                 weather_target_changes=sum(r['family']=='weather_transition' and r['result']['target_changed'] for r in records),
                 weather_unmet_target_removals=sum(r['family']=='weather_transition' and r['result']['unmet_target_removed'] for r in records))
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    lines=['# Catalogue des cas exécutés', '',
           'Chaque ligne décrit un cas effectivement exécuté. PASS signifie uniquement que les invariants indiqués dans cases.json passent.',
           'FAIL désigne un écart à une exigence de composant ou de produit ; ce n’est pas un nombre de pannes physiques.', '',
           '| ID | Entrées | Contrôles en échec |', '|---|---|---|']
    for r in records:
        inputs=json.dumps(r['inputs'],ensure_ascii=False,default=str).replace('|','/')
        lines.append(f"| {r['id']} | {inputs} | {', '.join(r['failed']) or 'PASS'} |")
    (args.output/'catalogue.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    return bool(failed)


if __name__ == '__main__':
    sys.exit(main())
