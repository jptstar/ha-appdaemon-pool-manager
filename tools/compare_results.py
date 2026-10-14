"""Compare two result files (jsonl) job by job, ignoring wall-clock timing.

Exit status 1 when a common job differs in any deterministic field, trace included.
"""
import argparse
import json
from pathlib import Path

IGNORED = {'real_seconds'}


def load(path):
    rows = {}
    for line in Path(path).read_text().splitlines():
        row = json.loads(line)
        rows[row['id']] = {k: v for k, v in row.items() if k not in IGNORED}
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline')
    parser.add_argument('candidate')
    args = parser.parse_args()
    a, b = load(args.baseline), load(args.candidate)
    common = sorted(set(a) & set(b))
    different = [key for key in common if a[key] != b[key]]
    print(json.dumps(dict(baseline=len(a), candidate=len(b), common=len(common),
                          identical=len(common)-len(different), different=different[:20])))
    return 1 if different or not common else 0


if __name__ == '__main__':
    raise SystemExit(main())
