"""Resume the complete finite matrix with bounded, isolated worker processes.

No HA connection. Checkpoints every result, refuses mixed source versions and
duplicate supervisors. Sleep pauses computation; rerunning resumes completed IDs.
"""
import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import datetime as dt
import fcntl
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import sys
import time

from simulate_pool import DOMAINS, ROOT, simulate


TOTAL = math.prod(len(values) for values in DOMAINS.values())
STRIDE = 104729  # coprime with 540000: visit every ID, spread weather/modes early


def case_at(index):
    if not 0 <= index < TOTAL:
        raise ValueError('case index outside matrix')
    case = {}
    for key, values in reversed(list(DOMAINS.items())):
        index, position = divmod(index, len(values))
        case[key] = values[position]
    return {key: case[key] for key in DOMAINS}


def fingerprint():
    digest = hashlib.sha256()
    paths = sorted((ROOT/'apps/pool_manager').glob('*.py'))
    paths += [ROOT/'tools/simulate_pool.py', Path(__file__).resolve(),
              ROOT/'examples/filtration_piscine.yaml']
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run_case(index, hours, step_s):
    begin = time.perf_counter()
    case = case_at(index)
    try:
        result = simulate(case, hours=hours, step_s=step_s)
    except Exception as error:
        result = {'case': case, 'violations': ['simulation_exception'], 'error': repr(error)}
    result.update(id=index, real_seconds=round(time.perf_counter()-begin, 4))
    return result


def atomic_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    temporary.replace(path)


def read_checkpoint(path):
    completed, violations = set(), Counter()
    failed = deficit = 0
    if not path.exists():
        return completed, violations, failed, deficit
    # Only a trailing incomplete write from an interrupted supervisor may be
    # discarded. A corrupt committed line is an error, never silently ignored.
    with path.open('r+b') as stream:
        while True:
            offset = stream.tell()
            line = stream.readline()
            if not line:
                break
            if not line.endswith(b'\n'):
                stream.seek(offset)
                stream.truncate()
                break
            result = json.loads(line)
            index = result['id']
            if not 0 <= index < TOTAL or index in completed:
                raise ValueError(f'duplicate/invalid completed ID: {index}')
            if result['case'] != case_at(index):
                raise ValueError(f'checkpoint case does not match ID: {index}')
            completed.add(index)
            violations.update(result['violations'])
            failed += bool(result['violations'])
            deficit += (result.get('comfort_deficit_c') or 0) > 0
    return completed, violations, failed, deficit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/full-matrix-2026-10-04')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--hours', type=float, default=48)
    parser.add_argument('--step-s', type=int, default=300)
    parser.add_argument('--limit', type=int, default=0, help='Additional cases this run; 0 = all remaining')
    args = parser.parse_args()
    if not 1 <= args.workers <= 4 or args.hours <= 0 or not 1 <= args.step_s <= 300 or args.limit < 0:
        parser.error('workers 1..4, hours > 0, step-s 1..300, limit >= 0 required')
    if math.gcd(STRIDE, TOTAL) != 1:
        raise ValueError('visit order must be a permutation of the entire matrix')
    args.output.mkdir(parents=True, exist_ok=True)
    lock = (args.output/'supervisor.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('a supervisor already owns this output directory')
    source = fingerprint()
    manifest = {'source_sha256': source, 'domains': DOMAINS, 'total': TOTAL,
                'hours': args.hours, 'step_s': args.step_s,
                'python': sys.version.split()[0], 'visit_stride': STRIDE}
    manifest_path = args.output/'manifest.json'
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if previous != manifest:
            parser.error('source/configuration changed: use a NEW output directory; results cannot be mixed')
    else:
        if (args.output/'results.jsonl').exists():
            parser.error('checkpoint exists without a manifest; refusing to claim its provenance')
        atomic_json(manifest_path, manifest)
    completed, violations, failed, deficits = read_checkpoint(args.output/'results.jsonl')
    initial_count = len(completed)
    started = time.monotonic()
    progress_path = args.output/'progress.json'
    def progress(state):
        elapsed = time.monotonic()-started
        count = len(completed)
        rate = (count-initial_count)/elapsed if elapsed else 0
        data = {'pid': os.getpid(), 'state': state, 'updated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                'source_sha256': source, 'total': TOTAL, 'completed': count,
                'remaining': TOTAL-count, 'failed_cases': failed, 'violations': dict(violations),
                'comfort_deficit_cases': deficits, 'workers': args.workers,
                'hours_each': args.hours, 'step_s': args.step_s,
                'session_elapsed_seconds': round(elapsed, 2), 'session_completed': count-initial_count,
                'estimated_remaining_seconds': round((TOTAL-count)/rate) if rate and count-initial_count >= 20 else None,
                'exhaustive': count == TOTAL}
        atomic_json(progress_path, data)
        return data
    progress('running')
    indices = (ordinal*STRIDE % TOTAL for ordinal in range(TOTAL))
    remaining = (index for index in indices if index not in completed)
    if args.limit:
        import itertools
        remaining = itertools.islice(remaining, args.limit)
    iterator = iter(remaining)
    submitted = set()
    stopped = False
    with ProcessPoolExecutor(max_workers=args.workers,
                             mp_context=multiprocessing.get_context('spawn'),
                             max_tasks_per_child=200) as pool:
        pending = {}
        def submit():
            index = next(iterator, None)
            if index is None:
                return False
            if index in submitted:
                raise ValueError('visit order duplicated an ID')
            submitted.add(index)
            pending[pool.submit(run_case, index, args.hours, args.step_s)] = index
            return True
        for _ in range(args.workers):
            submit()
        with (args.output/'results.jsonl').open('a') as stream:
            while pending:
                done, _ = wait(pending, timeout=10, return_when=FIRST_COMPLETED)
                for future in done:
                    index = pending.pop(future)
                    result = future.result()
                    if result['id'] != index:
                        raise ValueError('worker returned wrong case ID')
                    stream.write(json.dumps(result, ensure_ascii=False)+'\n')
                    stream.flush()
                    completed.add(index)
                    violations.update(result['violations'])
                    failed += bool(result['violations'])
                    deficits += (result.get('comfort_deficit_c') or 0) > 0
                if fingerprint() != source:
                    stopped = True
                    progress('stopped_source_changed')
                    break
                for _ in done:
                    submit()
                snapshot = progress('running')
                if done and ((len(completed)-initial_count) % 25 == 0 or result['violations']):
                    print(json.dumps(snapshot, ensure_ascii=False), flush=True)
    state = 'stopped_source_changed' if stopped else 'complete' if len(completed) == TOTAL else 'paused_limit'
    print(json.dumps(progress(state), ensure_ascii=False), flush=True)
    return bool(failed or stopped)


if __name__ == '__main__':
    sys.exit(main())
