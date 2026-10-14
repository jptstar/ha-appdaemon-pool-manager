"""Replay a deterministic sample of the balanced jobs and save full results.

Used for differential testing: run once on a baseline checkout and once on a
candidate checkout, then compare with tools/compare_results.py. Not a campaign.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
from pathlib import Path

import balanced_scenarios
from run_strategic_matrix import run_job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--every', type=int, default=8, help='take jobs[offset::every]')
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sample = balanced_scenarios.jobs()[args.offset::args.every]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn'),
                             max_tasks_per_child=50) as pool, args.output.open('w') as stream:
        for result in pool.map(run_job, sample):
            stream.write(json.dumps(result, ensure_ascii=False)+'\n'); stream.flush()
    print(json.dumps(dict(jobs=len(sample))))


if __name__ == '__main__':
    main()
