import importlib.util
import itertools
import json
import math
from pathlib import Path
import subprocess
import sys

TOOLS = Path(__file__).parents[1]/'tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('run_full_matrix', TOOLS/'run_full_matrix.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
from simulate_pool import cases


def test_index_and_permutation_preserve_every_cartesian_combination():
    indices = [0, 1, 104729, 539999]
    for index, case in enumerate(cases()):
        if index in indices:
            assert runner.case_at(index) == case
    assert math.gcd(runner.STRIDE, runner.TOTAL) == 1


def test_incomplete_trailing_write_is_repaired_without_losing_completed_case(tmp_path):
    path = tmp_path/'results.jsonl'
    valid = dict(id=0, case=runner.case_at(0), violations=[], comfort_deficit_c=0)
    path.write_text(json.dumps(valid)+'\n'+ '{"id": 10')
    completed, violations, failed, deficit = runner.read_checkpoint(path)
    assert completed == {0}
    assert not violations and failed == deficit == 0
    assert path.read_text() == json.dumps(valid)+'\n'


def test_resume_avoids_duplicate_cases_and_refuses_changed_configuration(tmp_path):
    command = [sys.executable, str(TOOLS/'run_full_matrix.py'), '--workers', '2',
               '--hours','1','--limit','4','--output',str(tmp_path)]
    for expected in (4, 8):
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr+result.stdout
        progress = json.loads((tmp_path/'progress.json').read_text())
        assert progress['completed'] == expected
        assert not progress['exhaustive']
    entries = [json.loads(line) for line in (tmp_path/'results.jsonl').read_text().splitlines()]
    assert len({entry['id'] for entry in entries}) == 8
    rejected = subprocess.run(command+['--hours','2'], capture_output=True, text=True, timeout=30)
    assert rejected.returncode != 0
    assert 'source/configuration changed' in rejected.stderr
    assert len((tmp_path/'results.jsonl').read_text().splitlines()) == 8
