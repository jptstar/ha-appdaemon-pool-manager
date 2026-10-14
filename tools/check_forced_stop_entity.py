"""Forced-stop *entity* check (offline, fake Home Assistant).

The simulator hard-wires the arret_force input to 'off' and unit tests stub
arret_force_actif(), so the real forced-stop switch is otherwise never
exercised. This forces it on for sampled fuzz cases and requires the pump and
the PAC to be off after a short settling time (2 steps).

Known behavior, identical in v0.10.17: in Hors Gel mode the antifreeze mixing
keeps running although the forced-stop switch is on. Those cases are reported
separately (`hors_gel_runs_despite_forced_stop`) and do not fail the check; it
is a safety-policy question for the owner, not something to change silently.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing

from run_fuzz_campaign import make_case
from simulate_pool import simulate

import pool_devices  # noqa: E402  (apps path is added by simulate_pool)

SETTLE_STEPS = 2


def check(index):
    job = make_case('forced-stop', index)
    owner = next(cls for cls in vars(pool_devices).values()
                 if isinstance(cls, type) and 'arret_force_actif' in vars(cls))
    original = owner.arret_force_actif
    owner.arret_force_actif = lambda self: True
    try:
        result = simulate(job['case'], hours=job['hours'], step_s=job['step_s'], trace=True)
    finally:
        owner.arret_force_actif = original
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
    failed = [r for r in rows if r['bad_steps'] and r['mode'] != 'Hors Gel']
    hors_gel = sum(1 for r in rows if r['bad_steps'] and r['mode'] == 'Hors Gel')
    print(json.dumps(dict(cases=len(rows), failed=len(failed), hors_gel_runs_despite_forced_stop=hors_gel,
                          examples=failed[:6]), ensure_ascii=False))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
