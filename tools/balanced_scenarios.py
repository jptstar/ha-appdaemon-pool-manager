"""Measured 3-way coverage plus exhaustive targeted 4-way slices and timelines."""
from functools import lru_cache
import itertools
import random

from simulate_pool import DOMAINS, pairwise_cases

GROUPS = list(itertools.combinations(DOMAINS, 3))


def requirements():
    return {(group, values) for group in GROUPS
            for values in itertools.product(*(DOMAINS[key] for key in group))}


def covered(case):
    return {(group, tuple(case[key] for key in group)) for group in GROUPS}


@lru_cache(maxsize=1)
def covering_cases():
    result = pairwise_cases()
    remaining = requirements()
    for case in result:
        remaining -= covered(case)
    rng = random.Random(1015)
    while remaining:
        candidates = [{key: rng.choice(values) for key, values in DOMAINS.items()}
                      for _ in range(60)]
        group, values = min(remaining, key=repr)
        forced = dict(candidates[0]); forced.update(zip(group, values))
        candidates.append(forced)
        best = max(candidates, key=lambda case: len(covered(case) & remaining))
        remaining -= covered(best)
        result.append(best)
    return result


def jobs():
    from run_strategic_matrix import jobs as quick_jobs
    baseline = {key: values[0] for key, values in DOMAINS.items()}
    baseline.update(water=29., heating='Fin de saison • Smart', solar='strong')
    result = []
    seen = set()
    def add(group, case, hours=48, step_s=300):
        import json
        signature = json.dumps([case, hours, step_s], sort_keys=True)
        if signature in seen:
            return
        seen.add(signature)
        result.append(dict(id=f'{group}-{len(result)}', group=group,
                           case=case, hours=hours, step_s=step_s))
    for job in quick_jobs():
        if not job['id'].startswith('coverage-'):
            add(job['id'].split('-')[0], dict(job['case']), job['hours'], job['step_s'])
    for case in covering_cases():
        add('all-triples', dict(case))
    # These two sensitive 4-way slices are fully enumerated; other axes remain
    # at the documented baseline. This is NOT full 4-way global coverage.
    for weather, cover, choice, fault in itertools.product(
            DOMAINS['weather'], DOMAINS['cover'], DOMAINS['choice'], DOMAINS['fault']):
        add('weather-cover-permission-fault', dict(baseline, weather=weather,
            cover=cover, choice=choice, fault=fault, solar='clouds', load=8000.))
    for heating, mode, solar, load in itertools.product(
            DOMAINS['heating'], DOMAINS['mode'], DOMAINS['solar'], DOMAINS['load']):
        add('pac-filtration-energy', dict(baseline, heating=heating,
                                        mode=mode, solar=solar, load=load))
    starts = ['2026-10-02T08:00:00', '2026-10-02T18:00:00',
              '2026-10-03T00:00:00', '2026-10-03T10:00:00']
    for start, hour, water, cover, weather, choice in itertools.product(
            starts, [12,17], [26.,29.,30.79], ['closed','open'],
            ['weekend_target','forecast_shift','oscillating'], DOMAINS['choice']):
        add('deadline', dict(baseline, start_time=start, target_hour=hour,
            water=water, cover=cover, weather=weather, choice=choice))
    turbo = [('Turbo • 1 h',1), ('Turbo • 2 h',2), ('Turbo • 3 h',3),
             ('Turbo • 6 h',6), ('Turbo • 12 h',12), ('Turbo • 1 jour',24),
             ('Turbo • 2 jours',48), ('Turbo • 3 jours',72)]
    for (heating, duration), start, cover, solar in itertools.product(
            turbo, starts, ['closed','cycling'], ['none','clouds']):
        add('turbo-expiry', dict(baseline, heating=heating, start_time=start,
            cover=cover, solar=solar, fault='restart'), hours=max(48,duration+6))
    sequences = [
        [{'after_h': 6, 'choice':'night'}, {'after_h':10, 'choice':'eco'}],
        [{'after_h':12,'choice':'night'}, {'after_h':18,'choice':'skip'}, {'after_h':22,'choice':'smart'}],
        [{'after_h':6,'choice':'skip'}, {'after_h':10,'choice':'night'}],
        [{'after_h':8,'heating':'Turbo • 1 h'}, {'after_h':10,'choice':'eco'}],
        [{'after_h':16,'choice':'night'}, {'after_h':18,'choice':'eco'}, {'after_h':24,'choice':'night'}],
    ]
    for sequence, weather, cover, load in itertools.product(sequences,
            ['sun','rain','storm','hot_cold','oscillating','forecast_shift'],
            DOMAINS['cover'], DOMAINS['load']):
        add('decision-change', dict(baseline, decision_events=sequence,
            weather=weather, cover=cover, load=load, solar='clouds', fault='restart'))
    # Thermal threshold on both sides of the acceptable target (31 - 0.2).
    for water, choice, solar, cover in itertools.product(
            [30.79,30.8,30.81,30.94,30.95,31.01], DOMAINS['choice'],
            DOMAINS['solar'], ['closed','open']):
        add('temperature-boundary', dict(baseline, water=water,
                                        choice=choice, solar=solar, cover=cover))
    return result
