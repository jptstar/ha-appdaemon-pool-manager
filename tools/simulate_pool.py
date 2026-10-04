"""Offline closed-loop simulation of the actual FiltrationPiscine controller.

Finite Cartesian domains, deterministic IDs, virtual clock and HA scheduler.
Never connects to Home Assistant. Physics is deliberately independent of MPC.
"""
import argparse
import copy
import csv
import datetime as dt
import heapq
import itertools
import json
import math
import random
from pathlib import Path
import sys
import tempfile
import types
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/pool_manager'))
from pool_predictive import normalize_daily_forecast

DOMAINS = {
    'weather': ['sun', 'rain', 'storm', 'hot_cold', 'cold_hot', 'oscillating', 'missing', 'weekend_target', 'forecast_shift', 'freeze'],
    'water': [18., 26., 29., 30.7, 32.],
    'cover': ['closed', 'open', 'cycling', 'unknown'],
    'solar': ['none', 'strong', 'clouds'],
    'load': [500., 4000., 8000.],
    'heating': ['Désactivé', 'Automatique', 'Fin de saison • Smart', 'Début de saison • Smart', 'Turbo • 12 h'],
    'choice': ['eco', 'night', 'skip'],
    'mode': ['Intelligent', 'Température', 'Hors Gel', 'Marche Forcée', 'Arrêt Forcé'],
    'fault': ['none', 'probe', 'pac_idle', 'restart'],
}


class Clock:
    def __init__(self, now):
        self.now = now

    def install(self):
        clock = self
        class DateMeta(type):
            def __instancecheck__(cls, obj):
                return isinstance(obj, dt.date)
        class Date(dt.date, metaclass=DateMeta):
            @classmethod
            def today(cls):
                return clock.now.date()
        class TimeMeta(type):
            def __instancecheck__(cls, obj):
                return isinstance(obj, dt.datetime)
        class DateTime(dt.datetime, metaclass=TimeMeta):
            @classmethod
            def now(cls, tz=None):
                return clock.now.replace(tzinfo=tz) if tz else clock.now
            @classmethod
            def utcnow(cls):
                return clock.now
        shim = types.SimpleNamespace(datetime=DateTime, date=Date, time=dt.time,
                                     timedelta=dt.timedelta, timezone=dt.timezone)
        self.originals = []
        for name, module in list(sys.modules.items()):
            if name.startswith('pool_') or name == 'filtration_piscine':
                if getattr(module, 'datetime', None) is dt:
                    self.originals.append((module, module.datetime))
                    module.datetime = shim

    def restore(self):
        for module, original in self.originals:
            module.datetime = original


class FakeHass:
    """Only the AppDaemon API surface used by the production controller."""
    def get_state(self, entity=None, attribute=None, **kwargs):
        if entity is None:
            return copy.deepcopy(self.backend.states)
        entry = self.backend.states.get(entity, {'state': 'unavailable', 'attributes': {}})
        if attribute == 'all':
            return copy.deepcopy(entry)
        return entry['state'] if attribute is None else entry['attributes'].get(attribute)

    def set_state(self, entity, state=None, attributes=None, replace=False, **kwargs):
        self.backend.set(entity, state, attributes, replace)

    def log(self, message, **kwargs):
        self.backend.logs.append((self.backend.clock.now.isoformat(), str(message)))

    def listen_state(self, callback, entity, **kwargs):
        self.backend.listeners.append((callback, entity, kwargs))
        return len(self.backend.listeners)

    def listen_event(self, callback, event, **kwargs):
        self.backend.event_listeners.append((callback, event, kwargs))
        return len(self.backend.event_listeners)

    def run_in(self, callback, delay, **kwargs):
        return self.backend.schedule(callback, delay, kwargs)

    def run_every(self, callback, start, interval, **kwargs):
        return self.backend.schedule(callback, 0, kwargs, interval)

    def cancel_timer(self, handle, **kwargs):
        self.backend.cancelled.add(handle)

    def timer_running(self, handle):
        return handle not in self.backend.cancelled and any(e[1] == handle for e in self.backend.events)

    def sun_up(self):
        return 8 <= self.backend.clock.now.hour < 18

    def sunrise(self):
        now = self.backend.clock.now
        value = now.replace(hour=8, minute=0, second=0)
        return value if now < value else value + dt.timedelta(days=1)

    def sunset(self):
        now = self.backend.clock.now
        value = now.replace(hour=18, minute=0, second=0)
        return value if now < value else value + dt.timedelta(days=1)

    def call_service(self, service, **kwargs):
        service = service.replace('.', '/')
        entity = kwargs.get('entity_id')
        self.backend.calls.append((self.backend.clock.now.isoformat(), service, kwargs))
        if service == 'fan/set_percentage':
            self.backend.set(entity, 'on' if kwargs['percentage'] else 'off', {'percentage': kwargs['percentage']})
        elif service.endswith('/turn_on'):
            self.backend.set(entity, 'on')
        elif service.endswith('/turn_off'):
            self.backend.set(entity, 'off')
        elif service == 'climate/set_hvac_mode':
            self.backend.set(entity, kwargs['hvac_mode'])
        elif service == 'climate/set_preset_mode':
            self.backend.set(entity, attributes={'preset_mode': kwargs['preset_mode']})
        elif service == 'climate/set_temperature':
            self.backend.set(entity, attributes={'temperature': kwargs['temperature']})
        elif service.endswith('/set_value'):
            self.backend.set(entity, kwargs['value'])
        elif service.endswith('/select_option'):
            self.backend.set(entity, kwargs['option'])
        elif service == 'timer/start':
            duration = kwargs['duration']
            seconds = (sum(int(v)*60**i for i,v in enumerate(reversed(duration.split(':'))))
                       if isinstance(duration, str) else int(duration))
            end = self.backend.clock.now+dt.timedelta(seconds=seconds)
            old_handle = self.backend.timer_handles.pop(entity, None)
            if old_handle is not None:
                self.backend.cancelled.add(old_handle)
            self.backend.set(entity, 'active', {'finishes_at': end.isoformat(), 'remaining': str(dt.timedelta(seconds=seconds))})
            def finish_timer(_):
                self.backend.timer_handles.pop(entity, None)
                self.backend.set(entity, 'idle')
                for callback, event, options in list(self.backend.event_listeners):
                    if event == 'timer.finished' and options.get('entity_id', entity) == entity:
                        callback(event, {'entity_id': entity}, {})
            self.backend.timer_handles[entity] = self.backend.schedule(finish_timer, seconds, {})
        elif service in {'timer/cancel', 'timer/finish'}:
            handle = self.backend.timer_handles.pop(entity, None)
            if handle is not None:
                self.backend.cancelled.add(handle)
            self.backend.set(entity, 'idle')
        elif service.startswith('notify/'):
            pass  # record only; no real notifications
        else:
            raise NotImplementedError(f'Unsupported simulated service: {service}')


class Backend:
    def __init__(self, clock):
        self.clock = clock
        self.states, self.listeners, self.event_listeners = {}, [], []
        self.events, self.cancelled, self.logs, self.calls = [], set(), [], []
        self.timer_handles = {}
        self.sequence = 0

    def schedule(self, callback, delay, kwargs, repeat=0):
        self.sequence += 1
        heapq.heappush(self.events, (self.clock.now+dt.timedelta(seconds=delay),
                                    self.sequence, callback, kwargs, repeat))
        return self.sequence

    def set(self, entity, state=None, attributes=None, replace=False):
        old = self.states.get(entity, {'state': 'unavailable', 'attributes': {}})
        new = {'state': old['state'] if state is None else str(state),
               'attributes': {} if replace else dict(old['attributes'])}
        new['attributes'].update(attributes or {})
        self.states[entity] = new
        for callback, watched, options in list(self.listeners):
            attribute = options.get('attribute')
            before = old['state'] if not attribute else old['attributes'].get(attribute)
            after = new['state'] if not attribute else new['attributes'].get(attribute)
            if watched != entity or before == after or ('new' in options and options['new'] != after):
                continue
            def invoke(_, cb=callback, e=entity, a=attribute or 'state', b=before, n=after):
                cb(e, a, b, n, {})
            self.schedule(invoke, 0, {})

    def advance(self, end):
        count = 0
        while self.events and self.events[0][0] <= end:
            when, handle, callback, kwargs, repeat = heapq.heappop(self.events)
            if handle in self.cancelled:
                continue
            self.clock.now = when
            callback(kwargs)
            if repeat and handle not in self.cancelled:
                heapq.heappush(self.events, (when+dt.timedelta(seconds=repeat), handle, callback, kwargs, repeat))
            count += 1
            if count > 10000:
                raise RuntimeError('Callback feedback loop (>10000 callbacks per step)')
        self.clock.now = end


def weather(profile, hour):
    if profile == 'sun': return 29., 15., 'sunny'
    if profile == 'rain': return 21., 12., 'rainy'
    if profile == 'storm': return 25., 12., 'lightning-rainy'
    if profile == 'hot_cold': return weather('sun' if hour < 24 else 'rain', hour)
    if profile == 'cold_hot': return weather('rain' if hour < 24 else 'sun', hour)
    if profile == 'oscillating': return weather('sun' if int(hour//6)%2 == 0 else 'rain', hour)
    if profile in {'weekend_target', 'forecast_shift'}: return weather('rain' if hour < 16 else 'sun', hour)
    if profile == 'freeze': return 0., -5., 'snowy'
    return 25., 14., 'partlycloudy'


def simulate(case, *, hours=48, step_s=300, trace=False):
    # Inject a fake hassapi only for this offline executable, never for HA.
    previous_hassapi = sys.modules.get('hassapi')
    sys.modules['hassapi'] = types.SimpleNamespace(Hass=FakeHass)
    from filtration_piscine import FiltrationPiscine
    clock = Clock(dt.datetime.fromisoformat(case.get('start_time', '2026-10-02T08:00:00')))
    backend = Backend(clock)
    clock.install()
    records, violations = [], set()
    flow_violation_samples = []
    energy = {'pump_kwh': 0., 'pac_kwh': 0., 'grid_kwh': 0., 'export_kwh': 0.}
    water = float(case['water'])
    started = clock.now
    target_date = dt.date.fromisoformat(case.get('target_date', '2026-10-03'))
    target_hour = int(case.get('target_hour', 12))
    deadline = dt.datetime.combine(target_date, dt.time(target_hour))
    decision_events = sorted(case.get('decision_events', []), key=lambda event: event['after_h'])
    event_cursor = 0
    applied_events = []
    try:
        with tempfile.TemporaryDirectory(prefix='pool-sim-') as directory:
            args = yaml.safe_load((ROOT/'examples/filtration_piscine.yaml').read_text())['pool_manager']
            args.update(chauffage_predictif=True, restitution_inst_mode='export_positive',
                        chauffage_predictif_learning_file=str(Path(directory)/'learning.json'),
                        chauffage_predictif_mpc_pas_h=2,
                        entity_volet_piscine='cover.pool', pool_notify_service='notify.simulated')
            args.update(chauffage_predictif_heure_baignade_semaine=target_hour,
                        chauffage_predictif_heure_baignade_weekend=target_hour)
            for value in args.values():
                if isinstance(value, str) and value.startswith(('sensor.', 'input_number.', 'input_text.', 'input_boolean.', 'input_select.', 'input_datetime.', 'timer.', 'switch.', 'binary_sensor.')):
                    backend.set(value, '0')
            initial = {
                args['temperature_eau']: str(water), args['mem_temp']: str(water),
                args['mode_de_fonctionnement']: case['mode'], args['entity_mode_auto']: 'on',
                args['entity_chauffage']: 'Fin de saison • Smart' if case['heating'].startswith('Turbo') else case['heating'], args['arret_force']: 'off',
                args['coef']: '100', args['mode_calcul']: 'on', args['tempo_eau']: '300',
                args['duree_filtration_ete']: '10', args['h_pivot']: '12:00:00',
                args['duree_bras_hors_gel']: '5', args['intervalle_bras_hors_gel']: '60',
                args['entity_chauffage_timer']: 'idle', 'cover.pool': 'closed',
            }
            for entity, value in initial.items(): backend.set(entity, value)
            backend.set(args['cde_pompe'], 'off', {'percentage': 0})
            backend.set(args['entity_pac_climate'], 'off', {'temperature': 31., 'current_temperature': water, 'preset_mode': 'Smart'})

            def forecasts():
                rows = []
                elapsed = (clock.now-started).total_seconds()/3600
                for offset in range(3):
                    if case['weather'] == 'missing' and offset == 1: continue
                    high, low, condition = weather(case['weather'], elapsed+offset*24)
                    rows.append({'datetime': (clock.now+dt.timedelta(days=offset)).isoformat(),
                                 'temperature': high, 'templow': low, 'condition': condition,
                                 'wind_speed': 40 if 'lightning' in condition else 4,
                                 'precipitation': 15 if 'rain' in condition else 0})
                normalized = normalize_daily_forecast(rows)
                if case['weather'] == 'forecast_shift' and elapsed >= 6:
                    for row in normalized:
                        if row['date'] == target_date:
                            row.update(temperature=23, usage_temperature=23, condition='partlycloudy',
                                       score=48, usage_score=48, strategic_score=48)
                return normalized

            def launch():
                app = FiltrationPiscine()
                app.args, app.backend, app.name = args, backend, 'simulation'
                app.initialize()
                app._refresh_predictive_forecast = forecasts
                # Use the real expiry, not an artificially permanent permit.
                if not restart_done:
                    app._pool_decision_choice = case['choice']
                    app._pool_decision_choice_until = app._pool_choice_deadline(case['choice'], clock.now)
                    app._pool_decision_day = clock.now.date()
                    app._publish_pool_decision()
                    # Apply the explicit initial choice through the actual
                    # listener, so Turbo arms its real HA timer lifecycle.
                    backend.set(args['entity_chauffage'], case['heating'])
                return app

            high, low, _ = weather(case['weather'], 0)
            for key in ('entity_temperature_exterieure', 'entity_temperature_exterieure_moyenne_24h', 'entity_temperature_exterieure_moyenne_7j'):
                backend.set(args[key], (high+low)/2)
            restart_done = False
            app = launch()
            deadline_water = None
            for index in range(math.ceil(hours*3600/step_s)):
                elapsed = (clock.now-started).total_seconds()/3600
                while event_cursor < len(decision_events) and elapsed >= decision_events[event_cursor]['after_h']:
                    event = decision_events[event_cursor]
                    if 'choice' in event:
                        option = {'eco': 'Journée seulement', 'night': 'Autoriser cette nuit',
                                  'skip': "Suspendre aujourd'hui", 'smart': 'Suivre le plan Smart'}[event['choice']]
                        backend.set(app.entity_pool_decision_manual, option)
                    if 'heating' in event:
                        backend.set(args['entity_chauffage'], event['heating'])
                    applied_events.append(dict(event, time=clock.now.isoformat()))
                    event_cursor += 1
                high, low, condition = weather(case['weather'], elapsed)
                hour = clock.now.hour+clock.now.minute/60
                ambient = low+(high-low)*max(0., math.sin(math.pi*(hour-6)/18))
                cover = case['cover']
                if cover == 'cycling': cover = 'open' if 12 <= hour < 17 else 'closed'
                backend.set('cover.pool', cover)
                pv = max(0., 5000*math.sin(math.pi*(hour-8)/10)) if 8 <= hour < 18 else 0.
                if case['solar'] == 'none': pv = 0.
                if case['solar'] == 'clouds': pv *= .15 if index%4 < 2 else .9
                if 'rain' in condition: pv *= .15
                speed = float(app.get_state(args['cde_pompe'], attribute='percentage') or 0) if app.pompe_est_on() else 0.
                pump_w = 180+1100*(speed/100)**3 if speed else 2.
                requested = app._pac_state() == 'heat'
                preset = app.get_state(args['entity_pac_climate'], attribute='preset_mode')
                heating = requested and speed >= 47 and water < 30.95 and case['fault'] != 'pac_idle'
                pac_w = (2200 if preset == 'Turbo' else 1450) if heating else 4.
                if requested and speed < 47:
                    violations.add('pac_requested_without_minimum_flow')
                    if len(flow_violation_samples) < 10:
                        flow_violation_samples.append({'time': clock.now.isoformat(), 'speed_pct': speed,
                            'mode': app.get_state(args['mode_de_fonctionnement']),
                            'heating_mode': app.get_state(args['entity_chauffage']),
                            'predictive_requested': getattr(app, 'chauffage_predictif_heat_requested', None),
                            'recent_commands': backend.calls[-6:]})
                if case['mode'] == 'Arrêt Forcé' and (requested or speed): violations.add('forced_stop_not_respected')
                if case['heating'] == 'Désactivé' and requested: violations.add('disabled_heating_requested')
                # Independent first-order physical approximation, NOT MPC helpers.
                loss = max(0., water-ambient)*(.003 if cover == 'closed' else .009)
                gain = (.34 if preset == 'Turbo' else .27)*max(.45, min(1.2, (ambient+5)/30)) if heating else 0.
                solar_gain = pv/5000*.018 if cover != 'closed' else 0.
                water += (gain+solar_gain-loss)*step_s/3600
                grid = float(case['load'])+pump_w+pac_w-pv
                for key, watts in [('pump_kwh', pump_w), ('pac_kwh', pac_w), ('grid_kwh', max(0., grid)), ('export_kwh', max(0., -grid))]:
                    energy[key] += watts*step_s/3600000
                values = {args['temperature_eau']: water,
                          args['entity_pompe_conso']: pump_w, args['entity_pac_conso']: pac_w,
                          args['restitution_inst']: max(0., -grid), args['entity_grid_import_power']: max(0., grid),
                          args['entity_pv_power']: pv, args['entity_temperature_exterieure']: ambient,
                          args['entity_pac_inlet_temperature']: water, args['entity_pac_outlet_temperature']: water+(1.5 if heating else 0),
                          args['entity_pac_ambient_temperature']: ambient}
                if case['fault'] == 'probe' and 12 <= elapsed < 14: values[args['temperature_eau']] = 'unavailable'
                for entity, value in values.items():
                    backend.set(entity, round(value, 3) if isinstance(value, (float, int)) else value)
                backend.set(args['entity_pac_climate'], attributes={'current_temperature': water, 'hvac_action': 'heating' if heating else 'idle'})
                if case['fault'] == 'restart' and elapsed >= 18 and not restart_done:
                    # HA timers survive an AppDaemon restart; app callbacks do not.
                    backend.events = [event for event in backend.events if event[1] in backend.timer_handles.values()]
                    heapq.heapify(backend.events)
                    backend.listeners.clear(); backend.event_listeners.clear()
                    restart_done = True
                    app = launch()
                backend.advance(clock.now+dt.timedelta(seconds=step_s))
                if deadline_water is None and clock.now >= deadline: deadline_water = water
                if trace:
                    plan = getattr(app, 'chauffage_predictif_last_plan', {}) or {}
                    records.append({'time': clock.now.isoformat(), 'water_c': round(water, 3), 'speed_pct': speed,
                                    'pac_w': pac_w, 'grid_w': round(grid), 'solar_w': round(pv),
                                    'action': plan.get('action'), 'comfort_status': plan.get('comfort_status'),
                                    'comfort_target_date': plan.get('comfort_target_date'),
                                    'comfort_reason': plan.get('comfort_reason'),
                                    'decision_choice': getattr(app, '_pool_decision_choice', None),
                                    'heating_mode': app.get_state(args['entity_chauffage']),
                                    'timer_state': app.get_state(args['entity_chauffage_timer'])})
            faults = [message for _, message in backend.logs if 'Erreur' in message or 'ERROR' in message]
            if faults: violations.add('controller_logged_error')
            return {'case': case, 'hours': hours, 'water_end_c': round(water, 3),
                    'saturday_noon_c': deadline_water, 'comfort_deficit_c': max(0., 30.8-deadline_water) if deadline_water is not None else None,
                    'deadline': deadline.isoformat(), 'water_at_deadline_c': deadline_water,
                    'applied_decision_events': applied_events,
                    'energy': {k: round(v, 3) for k,v in energy.items()}, 'violations': sorted(violations),
                    'errors': faults[:10], 'service_calls': len(backend.calls), 'trace': records,
                    'flow_violation_samples': flow_violation_samples}
    finally:
        clock.restore()
        if previous_hassapi is None: sys.modules.pop('hassapi', None)
        else: sys.modules['hassapi'] = previous_hassapi


def cases():
    for values in itertools.product(*DOMAINS.values()):
        yield dict(zip(DOMAINS, values))


def pairwise_cases(seed=731):
    """Deterministic covering array; every pair of declared values is covered.

    This is NOT exhaustive higher-order coverage. The Cartesian suite is kept
    for it. Forced candidates guarantee termination without probabilistic gaps.
    """
    rng = random.Random(seed)
    keys = list(DOMAINS)
    uncovered = {(a, va, b, vb) for a, b in itertools.combinations(keys, 2)
                 for va in DOMAINS[a] for vb in DOMAINS[b]}
    result = []
    while uncovered:
        candidates = [dict((k, rng.choice(v)) for k,v in DOMAINS.items()) for _ in range(100)]
        a, va, b, vb = min(uncovered, key=repr)
        forced = dict(candidates[0]); forced.update({a: va, b: vb})
        candidates.append(forced)
        def covered(case):
            return {(a, case[a], b, case[b]) for a,b in itertools.combinations(keys,2)} & uncovered
        best = max(candidates, key=lambda case: len(covered(case)))
        uncovered -= covered(best)
        result.append(best)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=16, help='0 = whole finite Cartesian matrix')
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--hours', type=float, default=48)
    parser.add_argument('--step-s', type=int, default=300, help='Physical integration step, 1..300 seconds')
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--catalogue-only', action='store_true', help='List all finite combinations without simulating them')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/closed-loop')
    parser.add_argument('--suite', choices=['smoke', 'pairwise', 'cartesian'], default='smoke')
    args = parser.parse_args()
    if args.hours <= 0 or not 1 <= args.step_s <= 300 or args.offset < 0 or args.limit < 0:
        parser.error('hours > 0, 1 <= step-s <= 300, offset/limit >= 0 required')
    total = math.prod(len(v) for v in DOMAINS.values())
    if args.catalogue_only:
        args.output.mkdir(parents=True, exist_ok=True)
        with (args.output/'catalogue.csv').open('w', newline='') as output:
            writer = csv.DictWriter(output, fieldnames=['id', *DOMAINS])
            writer.writeheader()
            for index, case in enumerate(cases()): writer.writerow({'id': index, **case})
        print(f'{total} combinations catalogued; none simulated')
        return 0
    baseline = {k: v[0] for k,v in DOMAINS.items()}
    baseline.update(water=29., heating='Fin de saison • Smart', solar='strong')
    if args.suite == 'smoke':
        source = [dict(baseline)] + [dict(baseline, **{key: value}) for key, domain in DOMAINS.items() for value in domain if value != baseline[key]]
    elif args.suite == 'pairwise': source = pairwise_cases()
    else: source = cases()
    end = args.offset+args.limit if args.limit else None
    args.output.mkdir(parents=True, exist_ok=True)
    completed = failed = 0
    with (args.output/'results.jsonl').open('w') as output:
        for index, case in enumerate(itertools.islice(source, args.offset, end), args.offset):
            try: result = simulate(case, hours=args.hours, step_s=args.step_s, trace=args.trace)
            except Exception as exc: result = {'case': case, 'violations': ['simulation_exception'], 'error': repr(exc)}
            result['id'] = index
            output.write(json.dumps(result, ensure_ascii=False)+'\n'); output.flush()
            completed += 1; failed += bool(result['violations'])
            print(f'{index}: {result["violations"] or "OK"}', flush=True)
    summary = {'domains': DOMAINS, 'cartesian_total': total, 'suite': args.suite,
               'offset': args.offset, 'executed': completed, 'failed': failed,
               'hours': args.hours, 'exhaustive': args.suite == 'cartesian' and args.offset == 0 and completed == total}
    summary['step_s'] = args.step_s
    (args.output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False))
    return bool(failed)


if __name__ == '__main__':
    sys.exit(main())
