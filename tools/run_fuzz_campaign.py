"""Seeded, boundary-biased fuzz campaign for the real controller (offline).

Complements the fixed 3-way matrix: short windows (8-16 h) and random start
times make each case cheap, so many more situations fit in the same CPU time.
Cases are a pure function of (seed, index): a run is reproducible and
resumable, and a failing case can be replayed from its index alone.

Oracles (hard failures unless noted):
  * every violation reported by the simulator (forced stop, PAC without flow,
    logged errors, decision events not applied, exceptions);
  * determinism: a sample of cases is run twice and must be identical;
  * fault isolation: a sample is rerun with one PAC-side safety stage raising
    on every tick and must show no violation beyond the plain run;
  * step consistency (WARNING only): the same case at a 60 s step should give
    the same violation set.
Not a calibrated physical model and not a proof of correctness.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import datetime as dt
import fcntl
import hashlib
import itertools
import json
import multiprocessing
import os
from pathlib import Path
import random
import signal
import time

from simulate_pool import DOMAINS, simulate
from run_full_matrix import atomic_json, fingerprint

WATER_BOUNDARIES = [14., 18., 25., 28., 29.9, 30.0, 30.5, 30.75, 30.8, 30.85, 30.95, 31.0, 31.5, 32.]
LOAD_BOUNDARIES = [0., 300., 1500., 2500., 3500., 4500., 6000., 9000.]
HOUR_BOUNDARIES = [0, 6, 7, 8, 9, 10, 12, 14, 16, 17, 18, 20, 22]
STEPS = [300, 300, 300, 120, 60]
INJECTED_STAGES = ['_protect_pac_flow', '_manage_pac_auto']


def make_case(seed, index):
    rng = random.Random(f'{seed}:{index}')
    case = {key: rng.choice(values) for key, values in DOMAINS.items()}
    case['water'] = rng.choice(WATER_BOUNDARIES) if rng.random() < .5 else round(rng.uniform(14., 32.), 2)
    case['load'] = rng.choice(LOAD_BOUNDARIES) if rng.random() < .4 else round(rng.uniform(0., 9000.), 0)
    day = dt.date(2026, 10, 1) + dt.timedelta(days=rng.randrange(0, 30))
    if rng.random() < .6:
        minutes = rng.choice(HOUR_BOUNDARIES)*60 + rng.randint(-6, 6)
    else:
        minutes = rng.randrange(0, 24*60)
    minutes %= 24*60
    start = dt.datetime.combine(day, dt.time(minutes//60, minutes % 60))
    hours = rng.choice([8, 12, 16])
    case['start_time'] = start.isoformat()
    case['target_date'] = (start.date()+dt.timedelta(days=1)).isoformat()
    case['target_hour'] = rng.choice([10, 12, 14, 18])
    events = []
    for _ in range(rng.choice([0, 0, 1, 2])):
        event = {'after_h': round(rng.uniform(.5, hours-.5), 2)}
        if rng.random() < .5:
            event['choice'] = rng.choice(['eco', 'night', 'skip', 'smart'])
        else:
            event['heating'] = rng.choice(DOMAINS['heating'][:3])
            if case['heating'] == 'Désactivé':
                # The simulator's disabled-heating oracle reads the initial
                # setting only; re-enabling heating mid-run is legitimate.
                del event['heating']
                event['choice'] = 'eco'
        events.append(event)
    if events:
        case['decision_events'] = events
    return dict(id=f'fuzz-{index}', case=case, hours=hours, step_s=rng.choice(STEPS))


def stable(result):
    return {key: value for key, value in result.items() if key not in ('real_seconds', 'trace')}


def run_injected(case, hours, step_s, stage):
    import pool_heating
    import pool_safety
    owner = {'_protect_pac_flow': pool_safety.SafetyMixin, '_manage_pac_auto': pool_heating.HeatingModeMixin}[stage]
    original = getattr(owner, stage)

    def fail(self, *args, **kwargs):
        raise RuntimeError(f'injected failure in {stage}')
    setattr(owner, stage, fail)
    try:
        return simulate(case, hours=hours, step_s=step_s)
    finally:
        setattr(owner, stage, original)


def run_job(job):
    started = time.monotonic()
    seed_index = int(job['id'].split('-')[1])
    case, hours, step_s = job['case'], job['hours'], job['step_s']
    hard, warnings = [], []
    try:
        result = simulate(case, hours=hours, step_s=step_s)
        hard += [f'violation:{name}' for name in result['violations']]
        if seed_index % 16 == 0:
            if stable(simulate(case, hours=hours, step_s=step_s)) != stable(result):
                hard.append('nondeterministic')
        if seed_index % 8 == 1:
            stage = INJECTED_STAGES[(seed_index//8) % len(INJECTED_STAGES)]
            injected = run_injected(case, hours, step_s, stage)
            extra = set(injected['violations'])-set(result['violations'])-{'controller_logged_error'}
            hard += [f'fault_isolation:{stage}:{name}' for name in sorted(extra)]
        if seed_index % 8 == 3 and step_s != 60:
            fine = simulate(case, hours=hours, step_s=60)
            if set(fine['violations']) != set(result['violations']):
                warnings.append('step_consistency:'+','.join(sorted(set(fine['violations'])^set(result['violations']))))
        summary = dict(violations=result['violations'], error=result.get('error'),
                       comfort_deficit_c=result.get('comfort_deficit_c'),
                       energy=result['energy'], service_calls=result['service_calls'])
    except Exception as error:
        hard.append('harness_exception')
        summary = dict(violations=['simulation_exception'], error=repr(error))
    return dict(id=job['id'], case=case, hours=hours, step_s=step_s, hard=hard, warnings=warnings,
                real_seconds=round(time.monotonic()-started, 3), **summary)


def source_hash():
    return hashlib.sha256((fingerprint()+Path(__file__).read_text()).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', type=int, default=4000)
    parser.add_argument('--seed', default='pool-0.10.19')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--fail-fast', action='store_true')
    parser.add_argument('--max-minutes', type=float, default=0, help='0 = no limit')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    lock = (args.output/'supervisor.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    source = source_hash()
    manifest = dict(source_sha256=source, seed=args.seed, cases=args.cases)
    manifest_path = args.output/'manifest.json'
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old['source_sha256'] != source or old['seed'] != args.seed or old['cases'] > args.cases:
            raise SystemExit('source/seed changed or fewer cases requested: use a new output directory')
    atomic_json(manifest_path, manifest)
    result_path = args.output/'results.jsonl'
    completed = {}
    if result_path.exists():
        with result_path.open('r+b') as stream:
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.endswith(b'\n'):
                    stream.seek(offset)
                    stream.truncate()
                    break
                row = json.loads(line)
                completed[row['id']] = {'hard': row['hard'], 'warnings': row['warnings']}
    initial, started = len(completed), time.monotonic()

    def progress(state):
        elapsed = time.monotonic()-started
        count = len(completed)
        rate = (count-initial)/elapsed if elapsed else 0
        data = dict(state=state, updated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(), pid=os.getpid(),
                    completed=count, total=args.cases, seed=args.seed,
                    failed_cases=sum(bool(r['hard']) for r in completed.values()),
                    warning_cases=sum(bool(r['warnings']) for r in completed.values()),
                    failed_ids=[key for key, r in completed.items() if r['hard']][:50],
                    session_elapsed_seconds=round(elapsed, 1),
                    estimated_remaining_seconds=round((args.cases-count)/rate) if count-initial >= 16 and rate else None)
        atomic_json(args.output/'progress.json', data)
        return data

    def on_signal(signum, frame):
        raise KeyboardInterrupt(f'signal {signum}')
    signal.signal(signal.SIGTERM, on_signal)
    deadline = time.monotonic()+args.max_minutes*60 if args.max_minutes else None
    pending = (make_case(args.seed, i) for i in range(args.cases) if f'fuzz-{i}' not in completed)
    progress('running')
    try:
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn'),
                                 max_tasks_per_child=200) as pool, result_path.open('a') as stream:
            while batch := list(itertools.islice(pending, args.workers*4)):
                if source_hash() != source:
                    progress('stopped_source_changed')
                    return 1
                for row in pool.map(run_job, batch):
                    stream.write(json.dumps(row, ensure_ascii=False)+'\n')
                    stream.flush()
                    completed[row['id']] = {'hard': row['hard'], 'warnings': row['warnings']}
                progress('running')
                if args.fail_fast and any(completed[job['id']]['hard'] for job in batch):
                    progress('stopped_violation')
                    return 1
                if deadline and time.monotonic() > deadline:
                    progress('stopped_time_budget')
                    return 0
    except BaseException:
        progress('interrupted')
        raise
    summary = progress('complete')
    atomic_json(args.output/'summary.json', summary)
    return 1 if summary['failed_cases'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
