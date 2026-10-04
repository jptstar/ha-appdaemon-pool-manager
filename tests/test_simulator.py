"""Keep the offline harness and its coverage claims verifiable."""
import importlib.util
import itertools
import json
from pathlib import Path
import subprocess
import sys
import datetime as dt

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('simulate_pool', ROOT/'tools/simulate_pool.py')
sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)


def test_pairwise_generator_really_covers_every_declared_pair():
    cases = sim.pairwise_cases()
    assert cases == sim.pairwise_cases()
    for a,b in itertools.combinations(sim.DOMAINS, 2):
        observed = {(case[a],case[b]) for case in cases}
        assert observed == set(itertools.product(sim.DOMAINS[a], sim.DOMAINS[b]))


def test_cartesian_count_and_stable_first_case():
    assert next(sim.cases()) == {key: domain[0] for key, domain in sim.DOMAINS.items()}
    assert sum(1 for _ in sim.cases()) == 540000


def test_real_controller_runs_offline_without_errors(tmp_path):
    run = subprocess.run([sys.executable, str(ROOT/'tools/simulate_pool.py'),
                          '--hours','1','--limit','1','--output',str(tmp_path)],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout+run.stderr
    result = json.loads((tmp_path/'results.jsonl').read_text())
    assert result['violations'] == []
    assert result['service_calls'] > 0
    assert result['energy']['grid_kwh'] >= 0
    assert not json.loads((tmp_path/'summary.json').read_text())['exhaustive']


def test_fake_ha_timer_emits_finished_event_and_cancel_prevents_it():
    clock = sim.Clock(dt.datetime(2026,10,2,8))
    backend = sim.Backend(clock)
    app = sim.FakeHass()
    app.backend = backend
    events = []
    app.listen_event(lambda name,data,kwargs: events.append(data['entity_id']),
                     'timer.finished', entity_id='timer.pool')
    app.call_service('timer/start', entity_id='timer.pool', duration='00:00:30')
    backend.advance(clock.now+dt.timedelta(seconds=30))
    assert events == ['timer.pool']
    assert app.get_state('timer.pool') == 'idle'
    app.call_service('timer/start', entity_id='timer.pool', duration='00:00:30')
    app.call_service('timer/cancel', entity_id='timer.pool')
    backend.advance(clock.now+dt.timedelta(seconds=30))
    assert events == ['timer.pool']
