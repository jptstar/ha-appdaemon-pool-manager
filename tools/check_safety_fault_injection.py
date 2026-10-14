"""Fault-injection check for the safety loop (offline, fake Home Assistant).

Makes a PAC-side stage raise on every tick and checks that pump control still
runs: filtration still happens, a forced stop is still respected and no
simulation exception escapes. Compare a baseline and a candidate checkout.
"""
import argparse
import json

import balanced_scenarios
import pool_heating
import pool_safety
from simulate_pool import simulate

# stage name -> mixin class that defines it
STAGES = {'_protect_pac_flow': pool_safety.SafetyMixin,
          '_manage_pac_auto': pool_heating.HeatingModeMixin}


def raising(name):
    def fail(self, *args, **kwargs):
        raise RuntimeError(f'injected failure in {name}')
    return fail


def run(case, stage):
    owner = STAGES[stage] if stage else None
    original = getattr(owner, stage) if stage else None
    if stage:
        setattr(owner, stage, raising(stage))
    try:
        result = simulate(case, hours=24, step_s=300)
    finally:
        if stage:
            setattr(owner, stage, original)
    return dict(stage=stage, violations=result['violations'], pump_kwh=result['energy']['pump_kwh'],
                service_calls=result['service_calls'], error=result.get('error'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=int, default=4)
    args = parser.parse_args()
    cases = [job['case'] for job in balanced_scenarios.jobs() if job['id'].startswith('regression')][:args.cases]
    rows, failed = [], False
    for index, case in enumerate(cases):
        reference = run(case, None)
        for stage in STAGES:
            injected = run(case, stage)
            controlled = injected['pump_kwh'] > 0.5*reference['pump_kwh'] and 'simulation_exception' not in injected['violations']
            forced = 'forced_stop_not_respected' not in injected['violations']
            ok = controlled and forced
            failed |= not ok
            rows.append(dict(case=index, stage=stage, ok=ok, reference_pump_kwh=reference['pump_kwh'],
                             injected_pump_kwh=injected['pump_kwh'], violations=injected['violations']))
    print(json.dumps(rows, indent=1))
    print(json.dumps(dict(checked=len(rows), failed=sum(not r['ok'] for r in rows))))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
