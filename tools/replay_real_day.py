"""Replay a recorded Home Assistant day through the real controller (offline).

Reads a Home Assistant history CSV export (columns entity_id,state,last_changed
...), takes the recorded solar production and house load, the helper values at
the start of the window, and runs the production controller in the simulator
with those inputs. The recorded pump episodes are printed next to the simulated
ones, so a defect seen on a real day (e.g. pump short-cycling) can be checked
against any version of the code.

The history file is personal data and is never copied into the repository.
Pool physics stay uncalibrated: only the control behavior is compared.
"""
import argparse
import csv
import datetime as dt
import json
import sys

from simulate_pool import simulate

PUMP_POWER = 'sensor.shelly_pompe_filtration_piscine_conso_inst'
SOLAR = 'sensor.production_solaire_conso_inst_positif'
HOUSE = 'sensor.maison_conso_inst'
QUOTA_DONE = 'input_number.piscine_temps_filtration_equivalent_jour'
QUOTA_TARGET = 'input_number.duree_filtration_ete'
VOLUME_DONE = 'input_number.piscine_volume_filtre_jour'
WATER = 'sensor.smart_next_water_temperature'


def load_history(path, utc_offset_h):
    series = {}
    with open(path, newline='') as stream:
        reader = csv.reader(stream)
        next(reader)
        for row in reader:
            if len(row) < 3:
                continue
            at = dt.datetime.fromisoformat(row[2].replace('Z', '+00:00')).replace(tzinfo=None)
            series.setdefault(row[0], []).append((at + dt.timedelta(hours=utc_offset_h), row[1]))
    for values in series.values():
        values.sort(key=lambda item: item[0])
    return series


def value_at(series, entity, at, default=None):
    last = default
    for when, raw in series.get(entity, []):
        if when > at:
            break
        try:
            last = float(raw)
        except ValueError:
            pass
    return last


def resample(series, entity, start, hours, step_s=60):
    points = []
    for index in range(int(hours*3600/step_s)+1):
        elapsed = index*step_s/3600
        points.append([round(elapsed, 5), value_at(series, entity, start+dt.timedelta(seconds=index*step_s), 0.0)])
    return points


def episodes(points, threshold):
    result, on = [], None
    for at, watts in points:
        if watts > threshold and on is None:
            on = at
        elif watts <= threshold and on is not None:
            result.append((on, at))
            on = None
    if on is not None:
        result.append((on, points[-1][0]))
    return result


def build_case(series, start, hours, heating, weather):
    target = value_at(series, QUOTA_TARGET, start, 10.0)
    return {
        'weather': weather, 'water': value_at(series, WATER, start, 22.0), 'cover': 'closed', 'solar': 'strong',
        'load': 0., 'heating': heating, 'choice': 'eco', 'mode': 'Intelligent', 'fault': 'none',
        'start_time': start.isoformat(),
        'target_date': (start.date()+dt.timedelta(days=1)).isoformat(), 'target_hour': 12,
        'pv_series': resample(series, SOLAR, start, hours),
        'load_series': resample(series, HOUSE, start, hours),
        'initial_args': {
            'entity_date_cumul': start.date().isoformat(),
            'entity_temps_filtration_equivalent_jour': str(value_at(series, QUOTA_DONE, start, 0.0)),
            'entity_volume_filtre_jour': str(value_at(series, VOLUME_DONE, start, 0.0)),
            'duree_filtration_ete': str(target),
        },
    }


def run(path, start, hours, utc_offset_h=2, heating='Fin de saison • Smart', weather='rain', step_s=60):
    series = load_history(path, utc_offset_h)
    case = build_case(series, start, hours, heating, weather)
    result = simulate(case, hours=hours, step_s=step_s, trace=True)
    sim_points = [[(dt.datetime.fromisoformat(r['time'])-start).total_seconds()/3600, r['speed_pct']]
                  for r in result['trace']]
    real_points = [[round((when-start).total_seconds()/3600, 5), value_at(series, PUMP_POWER, when, 0.0)]
                   for when in (start+dt.timedelta(seconds=i*60) for i in range(int(hours*60)+1))]
    return dict(
        simulated=[(a, b) for a, b in episodes(sim_points, 0)],
        recorded=[(a, b) for a, b in episodes(real_points, 50)],
        violations=result['violations'], pump_kwh=result['energy']['pump_kwh'],
        pac_kwh=result['energy']['pac_kwh'], start=start)


def describe(label, spans, start):
    print(f'{label}: {len(spans)} démarrages')
    for a, b in spans:
        begin = start+dt.timedelta(hours=a)
        print(f'   {begin:%H:%M} -> {(start+dt.timedelta(hours=b)):%H:%M}  ({(b-a)*60:4.0f} min)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv')
    parser.add_argument('--start', default='2026-10-14T09:52:00', help='local time, pump just stopped')
    parser.add_argument('--hours', type=float, default=8.3)
    parser.add_argument('--utc-offset', type=float, default=2)
    parser.add_argument('--heating', default='Fin de saison • Smart')
    parser.add_argument('--weather', default='rain')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    start = dt.datetime.fromisoformat(args.start)
    out = run(args.csv, start, args.hours, args.utc_offset, args.heating, args.weather)
    if args.json:
        print(json.dumps(dict(simulated_starts=len(out['simulated']), recorded_starts=len(out['recorded']),
                              violations=out['violations'], pump_kwh=out['pump_kwh'], pac_kwh=out['pac_kwh'])))
        return 0
    describe('Réel   ', out['recorded'], start)
    describe('Simulé ', out['simulated'], start)
    print(f"violations: {out['violations']} | pompe {out['pump_kwh']} kWh | PAC {out['pac_kwh']} kWh")
    return 0


if __name__ == '__main__':
    sys.exit(main())
