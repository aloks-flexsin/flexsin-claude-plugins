"""auto-runner baseline results (R3): the template validates, incomplete or inconsistent files are rejected by name,
and BASELINE.md states the model-version, variance and no-targets rules. No real run is made or asserted here."""
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner'
EVAL = PLUGIN / 'eval'
TEMPLATE = EVAL / 'results' / 'template.json'


def _mod(name, file):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / 'bin' / file)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


ev = _mod('autorunner_eval_r', 'autoeval.py')


def _template():
    return json.loads(TEMPLATE.read_text(encoding='utf-8'))


def _three_runs():
    """A synthetic, internally consistent three-run file (made-up numbers, used only to exercise the validator)."""
    data = _template()
    dims = list(ev.load_rubric(EVAL))
    runs = []
    for i, x in enumerate((0.5, 0.75, 1.0), 1):
        runs.append({'run': f'smoke-baseline-{i}', 'raw_result': f'raw/smoke-baseline-{i}.json',
                     'scores': {d: (x if d == 'functional_completeness' else None) for d in dims}})
    data.update(run_count=3, runs=runs,
                variance={d: ev.variance_of([r['scores'][d] for r in runs]) for d in dims})
    return data


def _err(data):
    with pytest.raises(ev.BenchmarkError) as e:
        ev.validate_results(data, EVAL)
    return str(e.value)


def test_template_validates():
    assert ev.validate_results(_template(), EVAL)['format'] == 'baseline.v1'


def test_validate_results_command(tmp_path, capsys):
    assert ev.main(['validate-results', str(TEMPLATE), '--eval-root', str(EVAL)]) == 0
    assert 'valid' in capsys.readouterr().out
    bad = _template()
    del bad['variance']
    f = tmp_path / 'bad.json'
    f.write_text(json.dumps(bad), encoding='utf-8')
    assert ev.main(['validate-results', str(f), '--eval-root', str(EVAL)]) == 2
    assert 'variance' in capsys.readouterr().err


def test_consistent_multi_run_file_validates():
    data = _three_runs()
    v = data['variance']['functional_completeness']
    assert v == {'n': 3, 'mean': 0.75, 'stdev': 0.25, 'min': 0.5, 'max': 1.0}
    assert data['variance']['cost'] == {'n': 0, 'mean': None, 'stdev': None, 'min': None, 'max': None}
    ev.validate_results(data, EVAL)


def test_missing_model_version_is_named():
    data = _template()
    del data['models'][0]['model_version']
    assert 'model_version' in _err(data)
    data['models'] = []
    assert 'models' in _err(data)


@pytest.mark.parametrize('bad', [None, 0, 'three', True])
def test_missing_or_bad_run_count_is_named(bad):
    data = _template()
    if bad is None:
        del data['run_count']
    else:
        data['run_count'] = bad
    assert 'run_count' in _err(data)


def test_run_count_must_match_runs():
    data = _three_runs()
    data['run_count'] = 2
    assert 'run_count' in _err(data)


def test_missing_variance_is_named():
    data = _template()
    del data['variance']
    assert 'variance' in _err(data)
    data = _template()
    del data['variance']['security']
    assert 'variance is missing security' in _err(data)
    data = _template()
    del data['variance']['cost']['stdev']
    assert 'variance cost is missing stdev' in _err(data)


def test_variance_that_disagrees_with_runs_is_rejected():
    data = _three_runs()
    data['variance']['functional_completeness']['mean'] = 0.9
    assert 'functional_completeness mean' in _err(data)


def test_targets_are_never_part_of_a_baseline():
    data = _template()
    data['targets'] = {'functional_completeness': 0.9}
    assert 'targets' in _err(data)


@pytest.mark.parametrize('cls', ['E0', 'E1', None])
def test_fake_or_unclassed_runs_are_not_a_baseline(cls):
    data = _template()
    data['evidence_class'] = cls
    assert 'evidence_class' in _err(data)


def test_other_missing_fields_are_named():
    for key in ('benchmark', 'plugin_version', 'date', 'operator'):
        data = _template()
        del data[key]
        assert key in _err(data)
    data = _template()
    data['benchmark'] = 'no-such-benchmark'
    assert 'unknown' in _err(data)
    data = _template()
    del data['runs'][0]['scores']['cost']
    assert 'missing cost' in _err(data)
    data = _template()
    data['runs'][0]['raw_result'] = '../outside.json'
    assert 'raw_result' in _err(data)


def test_validation_does_not_change_the_input():
    data = _three_runs()
    before = copy.deepcopy(data)
    ev.validate_results(data, EVAL)
    assert data == before


def test_baseline_doc_states_the_rules():
    text = (EVAL / 'BASELINE.md').read_text(encoding='utf-8')
    low = text.lower()
    assert 'model version' in low and 'model_version' in text
    assert 'variance' in low and 'stdev' in text and 'run_count' in text
    assert 'no targets before a baseline' in low
    assert 'never asserted by tests' in low
    assert 'validate-results' in text
