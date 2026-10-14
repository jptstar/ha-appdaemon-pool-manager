"""Forced-stop *entity* check (offline, fake Home Assistant).

The simulator hard-wires the arret_force input to 'off' and unit tests stub
arret_force_actif(), so the real forced-stop switch is otherwise never
exercised. This makes the *entity* read 'on' (through the fake Home Assistant
state API, so the production helper and every direct read see it) for sampled
fuzz cases and requires the pump and the PAC to be off after a short settling
time (2 steps), in every mode except Hors Gel, where freeze protection has
priority over the switch: the antifreeze mixing must keep running there.

`check_frost_priority` covers the continuous freeze protection
(hors_gel_adaptatif on, outdoor -5..0 C): the circulation must run in every
mode, with or without the forced-stop switch and with the Arrêt Forcé selector.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing

import simulate_pool
from run_fuzz_campaign import make_case
from simulate_pool import DOMAINS, simulate

import pool_safety  # noqa: E402  (apps path is added by simulate_pool)

SETTLE_STEPS = 2
MIXING_INTERVAL_MIN, MIXING_DURATION_MIN = 60, 5  # simulator args for Hors Gel mixing
FORCED_STOP_ENTITY = 'input_boolean.pool_force_stop'  # examples/filtration_piscine.yaml: arret_force


def check(index):
    job = make_case('forced-stop', index)
    original = simulate_pool.FakeHass.get_state

    def forced(self, entity=None, attribute=None, **kwargs):
        if entity == FORCED_STOP_ENTITY and attribute is None:
            return 'on'
        return original(self, entity, attribute, **kwargs)
    simulate_pool.FakeHass.get_state = forced
    try:
        result = simulate(job['case'], hours=job['hours'], step_s=job['step_s'], trace=True)
    finally:
        simulate_pool.FakeHass.get_state = original
    late = result['trace'][SETTLE_STEPS:]
    if job['case']['mode'] == 'Hors Gel':
        # Freeze protection wins: the pump must run at least half of the expected
        # mixing time (a continuous run, e.g. for the PAC, also qualifies).
        on_steps = sum(1 for record in late if record['speed_pct'] > 0)
        expected = job['hours']*60/(MIXING_INTERVAL_MIN+MIXING_DURATION_MIN)
        bad = [dict(reason='no antifreeze mixing', on_steps=on_steps, expected=round(expected, 1))] if on_steps < expected/2 else []
    else:
        bad = [dict(time=r['time'], speed_pct=r['speed_pct'], pac_w=r['pac_w']) for r in late
               if r['speed_pct'] > 0 or r['pac_w'] > 10]
    return dict(index=index, mode=job['case']['mode'], weather=job['case']['weather'],
                steps=len(late), bad_steps=len(bad), first_bad=bad[:3])


def check_frost_priority(index):
    """Continuous freeze protection must override Arrêt Forcé (selector or switch)."""
    job = make_case('frost-priority', index)
    mode = DOMAINS['mode'][index % len(DOMAINS['mode'])]
    switch_on = (index // len(DOMAINS['mode'])) % 2 == 1
    case = dict(job['case'], mode=mode, weather='freeze', fault='none', heating='Désactivé')
    case.pop('decision_events', None)
    original_get = simulate_pool.FakeHass.get_state
    original_init = pool_safety.SafetyMixin._initialize_safety

    def get_state(self, entity=None, attribute=None, **kwargs):
        if switch_on and entity == FORCED_STOP_ENTITY and attribute is None:
            return 'on'
        return original_get(self, entity, attribute, **kwargs)

    def initialize(self):
        self.args['hors_gel_adaptatif'] = 'true'
        return original_init(self)
    simulate_pool.FakeHass.get_state = get_state
    pool_safety.SafetyMixin._initialize_safety = initialize
    try:
        result = simulate(case, hours=12, step_s=300, trace=True)
    finally:
        simulate_pool.FakeHass.get_state = original_get
        pool_safety.SafetyMixin._initialize_safety = original_init
    late = result['trace'][SETTLE_STEPS:]
    off = [r for r in late if r['speed_pct'] <= 0]
    return dict(index=index, mode=mode, switch_on=switch_on, steps=len(late), bad_steps=len(off),
                first_bad=[dict(time=r['time']) for r in off[:3]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=int, default=200)
    parser.add_argument('--frost-cases', type=int, default=20)
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn')) as pool:
        rows = list(pool.map(check, range(args.cases)))
        rows += list(pool.map(check_frost_priority, range(args.frost_cases)))
    failed = [r for r in rows if r['bad_steps']]
    print(json.dumps(dict(cases=len(rows), failed=len(failed), examples=failed[:6]), ensure_ascii=False))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
