"""Coverage is proved against declared requirements, not inferred from a count."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT/'tools'))
spec = importlib.util.spec_from_file_location('strategic', ROOT/'tools/run_strategic_matrix.py')
strategic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strategic)


def test_selected_cases_cover_all_pairs_and_risky_triples():
    cases = strategic.covering_cases()
    observed = set().union(*(strategic.covered(case) for case in cases))
    assert strategic.requirements() <= observed
    assert len(cases) < 1000


def test_jobs_are_unique_and_include_fine_regressions_and_three_day_trajectories():
    jobs = strategic.jobs()
    assert len({job['id'] for job in jobs}) == len(jobs)
    assert sum(job['step_s'] == 30 for job in jobs) == 4
    assert sum(job['hours'] == 72 for job in jobs) == 24
    assert all(set(job['case']) == set(strategic.DOMAINS) for job in jobs)
