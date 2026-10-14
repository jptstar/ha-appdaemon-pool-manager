"""Antifreeze mixing check (offline, fake Home Assistant).

In Hors Gel mode with the forced stop off, the pump must be started
periodically for the circulation windows. The mutation test showed that
disabling this mixing was detected by no unit test and no campaign.

Pass criterion: over the window, the pump runs in at least half of the expected
cycles (hours * 60 / (interval + duration)), counted as distinct run episodes.
"""
import argparse
import json

from run_fuzz_campaign import make_case
from simulate_pool import simulate

INTERVAL_MIN, DURATION_MIN = 60, 5  # simulator args: intervalle/duree_bras_hors_gel


def episodes(trace):
    count, running = 0, False
    for record in trace:
        on = record['speed_pct'] > 0
        count += on and not running
        running = on
    return count


def run_cases(count):
    """Return the failing rows (empty when the antifreeze mixing works)."""
    rows = []
    for index in range(count):
        job = make_case('frost', index)
        case = dict(job['case'], mode='Hors Gel', weather='freeze', fault='none', heating='Désactivé')
        case.pop('decision_events', None)
        hours = 12
        result = simulate(case, hours=hours, step_s=300, trace=True)
        expected = hours*60/(INTERVAL_MIN+DURATION_MIN)
        seen = episodes(result['trace'])
        rows.append(dict(index=index, expected=round(expected, 1), episodes=seen, ok=seen >= expected/2,
                         violations=result['violations']))
    return [row for row in rows if not row['ok']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=int, default=24)
    args = parser.parse_args()
    failed = run_cases(args.cases)
    print(json.dumps(dict(cases=args.cases, failed=len(failed), examples=failed[:5])))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
