"""Replay the six v0.10.16 comfort regressions against the earlier campaign.

This is a numerical regression guard for the offline physics, not a guarantee
of a real-world bathing temperature. Original shortfalls remain diagnostic.
"""
import argparse
import json
from pathlib import Path

from run_full_matrix import atomic_json, fingerprint
from run_strategic_matrix import run_job
from simulate_pool import ROOT


REGRESSIONS = {
    "temperature-boundary-2147", "temperature-boundary-2111",
    "temperature-boundary-2091", "temperature-boundary-2109",
    "temperature-boundary-2125", "all-triples-158",
    "deadline-1316",
}
DIAGNOSTICS = {
    "weather-cover-permission-fault-438", "trajectory-18", "deadline-1261",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=ROOT / "outputs/balanced-2026-10-04")
    parser.add_argument("--manifest", type=Path, default=ROOT / "outputs/release-v0.10.16-balanced/manifest.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = fingerprint()
    manifest = json.loads(args.manifest.read_text())
    jobs = {job["id"]: job for job in manifest["jobs"]}
    reference = {
        row["id"]: row for row in
        (json.loads(line) for line in (args.reference / "results.jsonl").read_text().splitlines())
    }
    results, failures = [], []
    for identifier in sorted(REGRESSIONS | DIAGNOSTICS):
        job = jobs[identifier]
        before = reference[identifier]
        if before["case"] != job["case"]:
            raise ValueError(f"Mismatched reference case: {identifier}")
        result = run_job(job)
        delta = result["comfort_deficit_c"] - before["comfort_deficit_c"]
        result.update(reference_deficit_c=before["comfort_deficit_c"], deficit_delta_c=delta)
        results.append(result)
        if result["violations"] or (identifier in REGRESSIONS and delta > 0.05):
            failures.append(identifier)
        print(f"{identifier}: deadline={result['water_at_deadline_c']:.3f} C, "
              f"deficit delta={delta:+.3f} C, violations={result['violations']}", flush=True)
    if fingerprint() != source:
        failures.append("source_changed")
    atomic_json(args.output, dict(source_sha256=source, results=results, failures=failures,
                                  physical_model_calibrated=False))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
