"""Report whether a validation campaign is really alive.

progress.json can say "running" long after its process died (SIGKILL, sleep,
crash). This checks the recorded pid and the age of the last update.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path


def status(output, stale_after_s=300):
    data = json.loads((output/'progress.json').read_text())
    age = (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(data['updated_at_utc'])).total_seconds()
    pid = data.get('pid')
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            pass
        except PermissionError:
            alive = True
    if data['state'] == 'running' and pid and not alive:
        verdict = 'DEAD (state says running, process is gone)'
    elif data['state'] == 'running' and age > stale_after_s:
        verdict = f'STALLED (no update for {int(age)} s)'
    else:
        verdict = data['state']
    return dict(verdict=verdict, completed=data['completed'], total=data['total'],
                failed_cases=data['failed_cases'], age_s=int(age), pid=pid, alive=alive)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--stale-after', type=int, default=300)
    args = parser.parse_args()
    print(json.dumps(status(args.output, args.stale_after)))


if __name__ == '__main__':
    main()
