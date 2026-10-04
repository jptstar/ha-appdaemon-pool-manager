"""Offline targeted coverage; pairs + explicit risky triples + long trajectories.

Not Cartesian exhaustive, not a calibrated physical feasibility oracle.
Results are resumable only against identical source and scenario manifests.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import datetime as dt
import fcntl
import hashlib
import itertools
import json
import multiprocessing
from pathlib import Path
import random
import sys
import time

from simulate_pool import DOMAINS, ROOT, pairwise_cases, simulate
from run_full_matrix import atomic_json, fingerprint

TRIPLES = [
    ('weather', 'water', 'choice'),
    ('heating', 'mode', 'solar'),
    ('heating', 'mode', 'load'),
    ('heating', 'choice', 'fault'),
    ('weather', 'cover', 'fault'),
]


def requirements():
    groups = list(itertools.combinations(DOMAINS, 2)) + TRIPLES
    return {(group, values) for group in groups
            for values in itertools.product(*(DOMAINS[key] for key in group))}


def covered(case):
    groups = list(itertools.combinations(DOMAINS, 2)) + TRIPLES
    return {(group, tuple(case[key] for key in group)) for group in groups}


def covering_cases():
    rng = random.Random(108)
    result = pairwise_cases()
    remaining = requirements()
    for case in result:
        remaining -= covered(case)
    while remaining:
        candidates = [{key: rng.choice(values) for key, values in DOMAINS.items()}
                      for _ in range(100)]
        group, values = min(remaining, key=repr)
        forced = dict(candidates[0])
        forced.update(zip(group, values))
        candidates.append(forced)
        best = max(candidates, key=lambda case: len(covered(case) & remaining))
        remaining -= covered(best)
        result.append(best)
    return result


def jobs():
    result = []
    baseline = {key: values[0] for key, values in DOMAINS.items()}
    baseline.update(water=29., heating='Fin de saison • Smart', solar='strong')
    # Four historical circulation failures, repeated at fine physical sampling.
    for i, update in enumerate([
        dict(weather='forecast_shift', solar='none', load=8000., choice='night'),
        dict(heating='Turbo • 12 h', mode='Température', choice='night'),
        dict(heating='Turbo • 12 h', mode='Température', cover='cycling', fault='restart'),
        dict(heating='Turbo • 12 h', mode='Température', fault='probe'),
    ]):
        result.append(dict(id=f'regression-{i}', case=dict(baseline, **update), hours=48, step_s=30))
    for i, case in enumerate(covering_cases()):
        result.append(dict(id=f'coverage-{i}', case=case, hours=48, step_s=300))
    # Three days: several midnights, forecast revisions, timer expiry and cover cycles.
    for i, (weather, cover, choice) in enumerate(itertools.product(
            ['hot_cold', 'cold_hot', 'oscillating', 'forecast_shift', 'missing', 'storm'],
            ['closed', 'cycling'], ['eco', 'night'])):
        result.append(dict(id=f'trajectory-{i}', case=dict(baseline, weather=weather,
            cover=cover, choice=choice, solar='clouds', load=8000., fault='restart'),
            hours=72, step_s=300))
    return result


def run_job(job):
    started = time.monotonic()
    try:
        result = simulate(job['case'], hours=job['hours'], step_s=job['step_s'], trace=True)
    except Exception as error:
        result = dict(case=job['case'], violations=['simulation_exception'], error=repr(error))
    result.update(id=job['id'], step_s=job['step_s'],
                  real_seconds=round(time.monotonic()-started, 3))
    if len(result.get('applied_decision_events', [])) != len(job['case'].get('decision_events', [])) and 'simulation_exception' not in result['violations']:
        result['violations'].append('scheduled_decision_not_applied')
    if job.get('group') == 'turbo-expiry' and 'simulation_exception' not in result['violations']:
        if result['trace'] and result['trace'][-1]['heating_mode'].startswith('Turbo'):
            result['violations'].append('turbo_not_expired')
    # Keep thermal shortfalls separate from control failures. The physics is not
    # calibrated; reaching a temperature cannot establish energy optimality.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/strategic-2026-10-04')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--catalogue-only', action='store_true')
    parser.add_argument('--profile', choices=['quick','balanced'], default='quick')
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error('workers 1..4')
    args.output.mkdir(parents=True, exist_ok=True)
    lock = (args.output/'supervisor.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    selected = jobs()
    requirement_count = len(requirements())
    if args.profile == 'balanced':
        import balanced_scenarios
        selected = balanced_scenarios.jobs()
        requirement_count = len(balanced_scenarios.requirements())
    def source_hash():
        extra = (ROOT/'tools/balanced_scenarios.py').read_text() if args.profile == 'balanced' else ''
        return hashlib.sha256((fingerprint()+Path(__file__).read_text()+extra).encode()).hexdigest()
    source = source_hash()
    manifest = dict(source_sha256=source, jobs=selected, triples=TRIPLES,
                    profile=args.profile, python=sys.version.split()[0],
                    requirements=requirement_count,
                    total=len(selected), cartesian_total=540000, exhaustive=False)
    if args.profile == 'balanced':
        manifest['triples'] = balanced_scenarios.GROUPS
    manifest_path = args.output/'manifest.json'
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != json.loads(json.dumps(manifest)):
        parser.error('source/scenarios changed: use a new output directory')
    atomic_json(manifest_path, manifest)
    if args.catalogue_only:
        print(json.dumps(dict(total=len(selected), requirements=requirement_count, exhaustive=False)))
        return 0
    known = {job['id']: job for job in selected}
    completed = {}
    result_path = args.output/'results.jsonl'
    if result_path.exists():
        with result_path.open('r+b') as stream:
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.endswith(b'\n'):
                    stream.seek(offset); stream.truncate(); break
                result = json.loads(line)
                if result['id'] in completed or result['id'] not in known:
                    raise ValueError('invalid/duplicate checkpoint ID')
                job = known[result['id']]
                if result['case'] != job['case'] or result['step_s'] != job['step_s'] or result.get('hours',job['hours']) != job['hours']:
                    raise ValueError('checkpoint configuration mismatch')
                completed[result['id']] = {key:value for key,value in result.items() if key != 'trace'}
    initial = len(completed)
    started = time.monotonic()
    def progress(state):
        elapsed = time.monotonic()-started
        count = len(completed)
        rate = (count-initial)/elapsed if elapsed else 0
        data = dict(state=state, updated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
            completed=count, total=len(selected), failed_cases=sum(bool(r['violations']) for r in completed.values()),
            comfort_deficit_cases=sum((r.get('comfort_deficit_c') or 0)>0 for r in completed.values()),
            session_elapsed_seconds=round(elapsed,2), exhaustive=False,
            estimated_remaining_seconds=round((len(selected)-count)/rate) if count-initial>=12 else None)
        data['profile'] = args.profile
        data['failed_ids'] = [key for key,result in completed.items() if result['violations']]
        data['groups'] = {group: dict(total=sum(job.get('group',job['id'].split('-')[0]) == group for job in selected),
            completed=sum(job['id'] in completed and job.get('group',job['id'].split('-')[0]) == group for job in selected))
            for group in sorted({job.get('group',job['id'].split('-')[0]) for job in selected})}
        atomic_json(args.output/'progress.json', data)
        return data
    progress('running')
    remaining = iter(job for job in selected if job['id'] not in completed)
    # Only one batch per worker is queued. This avoids a costly unbounded queue
    # and checks source immutability between batches.
    with ProcessPoolExecutor(max_workers=args.workers,
            mp_context=multiprocessing.get_context('spawn'), max_tasks_per_child=100) as pool:
        with result_path.open('a') as stream:
            while batch := list(itertools.islice(remaining, args.workers)):
                if source_hash() != source:
                    progress('stopped_source_changed')
                    return 1
                for result in pool.map(run_job, batch):
                    stream.write(json.dumps(result, ensure_ascii=False)+'\n'); stream.flush()
                    completed[result['id']] = {key:value for key,value in result.items() if key != 'trace'}
                    print(json.dumps(progress('running')), flush=True)
    summary = progress('complete')
    atomic_json(args.output/'summary.json', summary)
    return bool(summary['failed_cases'])


if __name__ == '__main__':
    raise SystemExit(main())
