"""Prove declared triple coverage and exercise the new time/event harness."""
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import balanced_scenarios as balanced
import simulate_pool as sim


def test_every_declared_triple_and_pair_is_covered():
    cases = balanced.covering_cases()
    observed = set().union(*(balanced.covered(case) for case in cases))
    assert balanced.requirements() <= observed
    assert len(balanced.GROUPS) == 84
    import itertools
    for a,b in itertools.combinations(sim.DOMAINS,2):
        assert {(case[a],case[b]) for case in cases} == set(itertools.product(sim.DOMAINS[a],sim.DOMAINS[b]))


def test_balanced_size_unique_timelines_and_all_turbo_durations():
    jobs = balanced.jobs()
    assert 1500 <= len(jobs) <= 3000
    assert len({job['id'] for job in jobs}) == len(jobs)
    turbo = [job for job in jobs if job['group'] == 'turbo-expiry']
    assert len({job['case']['heating'] for job in turbo}) == 8
    assert all(job['hours'] >= 48 for job in jobs)
    deadline = [job for job in jobs if job['group'] == 'deadline']
    assert {job['case']['target_hour'] for job in deadline} == {12,17}
    assert len({job['case']['start_time'] for job in deadline}) == 4


def test_manual_night_choice_survives_restart_instead_of_reapplying_initial_eco():
    case = {key: values[0] for key,values in sim.DOMAINS.items()}
    case.update(water=29., heating='Fin de saison • Smart', fault='restart',
                decision_events=[{'after_h':6,'choice':'night'}])
    result = sim.simulate(case,hours=19,trace=True)
    assert result['violations'] == []
    assert len(result['applied_decision_events']) == 1
    assert result['trace'][-1]['decision_choice'] == 'night'


def test_variable_deadline_and_short_turbo_expiry_are_real():
    case = {key: values[0] for key,values in sim.DOMAINS.items()}
    case.update(water=29., heating='Turbo • 1 h', start_time='2026-10-03T10:00:00', target_hour=12)
    result = sim.simulate(case,hours=3,trace=True)
    assert result['violations'] == []
    assert dt.datetime.fromisoformat(result['deadline']) == dt.datetime(2026,10,3,12)
    assert result['water_at_deadline_c'] is not None
    assert not result['trace'][-1]['heating_mode'].startswith('Turbo')
