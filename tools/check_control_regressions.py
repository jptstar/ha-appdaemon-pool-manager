"""Replay every control failure from a preserved campaign, against current code."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
from pathlib import Path

from run_full_matrix import atomic_json, fingerprint
from run_strategic_matrix import run_job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("workers must be between 1 and 4")
    if args.output.exists():
        parser.error("output already exists; preserve earlier validation evidence")
    manifest = json.loads((args.campaign / "manifest.json").read_text())
    jobs = {job["id"]: job for job in manifest["jobs"]}
    historical = {}
    for line in (args.campaign / "results.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row["id"] in historical:
            raise ValueError(f"duplicate result ID: {row['id']}")
        historical[row["id"]] = row
    selected = []
    for identifier, row in sorted(historical.items()):
        if row.get("violations"):
            job = jobs[identifier]
            if job["case"] != row["case"]:
                raise ValueError(f"manifest/result mismatch: {identifier}")
            selected.append(job)
    if not selected:
        parser.error("no historical failures found; an empty replay is not validation")
    source = fingerprint()
    results = []
    with ProcessPoolExecutor(max_workers=args.workers,
                             mp_context=multiprocessing.get_context("spawn")) as pool:
        for result in pool.map(run_job, selected):
            results.append(result)
            print(f"{result['id']}: {result['violations']}", flush=True)
    failures = [row["id"] for row in results if row["violations"]]
    if source != fingerprint():
        failures.append("source_changed")
    atomic_json(args.output, dict(source_sha256=source, selected_ids=[j['id'] for j in selected],
                                  results=results, failures=failures,
                                  exhaustive=False, physical_model_calibrated=False))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
