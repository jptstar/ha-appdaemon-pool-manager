"""Forced-stop *entity* check (offline, fake Home Assistant).

The simulator hard-wires the arret_force input to 'off' and unit tests stub
arret_force_actif(), so the real forced-stop switch is otherwise never
exercised. This makes the *entity* read 'on' (through the fake Home Assistant
state API, so the production helper and every direct read see it) for sampled
fuzz cases and requires the pump and the PAC to be off after a short settling
time (2 steps), in every mode.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing

import simulate_pool
from run_fuzz_campaign import make_case
from simulate_pool import simulate

SETTLE_STEPS = 2
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
    bad = [dict(time=r['time'], speed_pct=r['speed_pct'], pac_w=r['pac_w']) for r in late
           if r['speed_pct'] > 0 or r['pac_w'] > 10]
    return dict(index=index, mode=job['case']['mode'], weather=job['case']['weather'],
                steps=len(late), bad_steps=len(bad), first_bad=bad[:3])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=int, default=200)
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn')) as pool:
        rows = list(pool.map(check, range(args.cases)))
    failed = [r for r in rows if r['bad_steps']]
    print(json.dumps(dict(cases=len(rows), failed=len(failed), examples=failed[:6]), ensure_ascii=False))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
