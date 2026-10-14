"""Exhaustive grid over the settings that drive the safety/filtration/PAC logic.

mode x heating x fault x choice x solar x water x weather = 27000 cases, 18 h
windows from the simulator's default start. Reuses the fuzz runner (same
oracles, resume, heartbeat, fail-fast) with a different case generator.
"""
import itertools
import sys

import run_fuzz_campaign as fuzz
from simulate_pool import DOMAINS

WEATHER = ['sun', 'storm', 'hot_cold', 'forecast_shift', 'missing', 'freeze']
GRID = list(itertools.product(DOMAINS['mode'], DOMAINS['heating'], DOMAINS['fault'], DOMAINS['choice'],
                              DOMAINS['solar'], DOMAINS['water'], WEATHER))


def grid_case(seed, index):
    mode, heating, fault, choice, solar, water, weather = GRID[index]
    case = dict(mode=mode, heating=heating, fault=fault, choice=choice, solar=solar, water=water,
                weather=weather, cover='cycling', load=4000.)
    return dict(id=f'fuzz-{index}', case=case, hours=18, step_s=300)


if __name__ == '__main__':
    fuzz.make_case = grid_case
    if '--cases' not in sys.argv:
        sys.argv += ['--cases', str(len(GRID))]
    raise SystemExit(fuzz.main())
