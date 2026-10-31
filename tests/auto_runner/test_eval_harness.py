"""auto-runner evaluation harness (R1): benchmark validation, held-out isolation, scoring with a fake runner, stable results."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner'
EVAL = PLUGIN / 'eval'


def _mod(name, file):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / 'bin' / file)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


ev = _mod('autorunner_eval_t', 'autoeval.py')


@pytest.fixture
def root(tmp_path):
    """A copy of the shipped eval folder (rubric and smoke benchmark) without results."""
    r = tmp_path / 'eval'
    shutil.copytree(EVAL, r, ignore=shutil.ignore_patterns('results'))
    return r


def bench_json(root, bid='smoke'):
    return root / 'benchmarks' / bid / 'benchmark.json'


def edit_bench(root, change, bid='smoke'):
    p = bench_json(root, bid)
    data = json.loads(p.read_text(encoding='utf-8'))
    change(data)
    p.write_text(json.dumps(data), encoding='utf-8')


def test_shipped_rubric_and_benchmark_validate():
    rubric = ev.load_rubric(EVAL)
    assert set(rubric) == set(ev.REQUIRED_DIMENSIONS)
    assert 'smoke' in ev.list_benchmarks(EVAL)
    b = ev.load_benchmark('smoke', EVAL)
    assert [h['id'] for h in b['heldout']] == ['H1', 'H2']
    assert ev.main(['validate', '--eval-root', str(EVAL)]) == 0


def test_fake_benchmark_runs_end_to_end_and_is_scored(root, tmp_path):
    result, path = ev.run('smoke', 'fake', root, results_dir=tmp_path / 'out')
    assert path == tmp_path / 'out' / 'smoke-fake.json'
    assert json.loads(path.read_text(encoding='utf-8')) == result
    assert result['format'] == 'result.v1' and result['evidence_class'] == 'E0'
    assert result['criteria']['AC1'] == {'status': 'passed', 'approved': True, 'negative': False, 'heldout': ['H1']}
    assert result['criteria']['AC2']['status'] == 'passed' and result['criteria']['AC2']['negative'] is True
    s = result['scores']
    assert set(s) == set(ev.REQUIRED_DIMENSIONS)
    assert s['functional_completeness']['value'] == 1.0
    assert s['business_correctness']['value'] == 1.0
    assert s['security']['value'] == 1.0
    assert s['incorrect_approval_rate']['value'] == 0.0
    for nothing in ('architecture', 'integration', 'reliability', 'operational_readiness'):
        assert s[nothing] == {'value': None, 'basis': 'nothing to measure'}
    for unreported in ('cost', 'human_rework', 'escaped_defects'):
        assert s[unreported] == {'value': None, 'basis': 'not reported'}
    assert result['owner'] == {'asked': 1, 'unanswered': 0, 'insufficient': 0,
                               'transcript': [{'question': 'name', 'answer': 'Ada', 'sufficient': True}]}


def test_wrong_delivery_and_false_approval_are_scored(root, tmp_path):
    def sloppy(workdir, view, owner):
        assert owner.ask('Which colour?') is None
        (workdir / 'greeting.txt').write_text('Hello, Bob\n', encoding='utf-8')
        (workdir / 'secrets.txt').write_text('x', encoding='utf-8')
        return {'approved': ['AC1', 'AC2'], 'cost_usd': 0.5, 'rework_minutes': 12}

    result, _ = ev.run('smoke', 'custom', root, results_dir=tmp_path, runner=sloppy)
    assert result['criteria']['AC1']['status'] == 'failed'
    assert result['heldout'][0]['reason'] == 'greeting.txt differs from the expected fixture'
    assert result['heldout'][1]['reason'] == 'secrets.txt exists'
    s = result['scores']
    assert s['functional_completeness']['value'] == 0.0
    assert s['security']['value'] == 0.0
    assert s['incorrect_approval_rate']['value'] == 1.0
    assert s['cost']['value'] == 0.5 and s['human_rework']['value'] == 12
    assert result['owner']['unanswered'] == 1
    assert result['evidence_class'] == 'unknown'


def test_runner_approval_alone_never_passes_a_criterion(root, tmp_path):
    (root / 'heldout' / 'smoke' / 'H1.json').write_text(json.dumps(
        {'id': 'H1', 'description': 'written only', 'criteria': ['AC1']}), encoding='utf-8')
    result, _ = ev.run('smoke', 'fake', root, results_dir=tmp_path)
    assert result['criteria']['AC1']['status'] == 'untested'
    assert result['heldout'][0]['status'] == 'not_run'
    assert result['scores']['functional_completeness']['basis'] == '1 of 1 tested criteria passed'


def test_agent_workdir_holds_no_heldout_file(root, tmp_path):
    seen = {}

    def spy(workdir, view, owner):
        seen['files'] = sorted(p.relative_to(workdir).as_posix() for p in workdir.rglob('*') if p.is_file())
        seen['bytes'] = {p.read_bytes() for p in workdir.rglob('*') if p.is_file()}
        seen['workdir'] = workdir.resolve()
        seen['view'] = view
        return {}

    ev.run('smoke', 'spy', root, results_dir=tmp_path / 'out', runner=spy)
    assert seen['files'] == ['criteria.json', 'requirement.md']
    heldout = list((root / 'heldout').rglob('*.json'))
    assert heldout and not ({h.read_bytes() for h in heldout} & seen['bytes'])
    assert root.resolve() not in seen['workdir'].parents
    assert set(seen['view']) == {'id', 'shape', 'requirement', 'criteria'}
    assert 'owner_answers' not in json.dumps(seen['view']) and 'Ada' not in json.dumps(seen['view'])


def test_isolation_check_refuses_a_leaked_heldout_file(root, tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    shutil.copyfile(root / 'heldout' / 'smoke' / 'H1.json', work / 'notes.json')
    with pytest.raises(ev.IsolationError, match='H1.json'):
        ev.check_isolated(work, root / 'heldout' / 'smoke')
    with pytest.raises(ev.IsolationError, match='overlaps'):
        ev.check_isolated(root / 'heldout', root / 'heldout' / 'smoke')


def test_missing_criterion_id_is_rejected_with_reason(root):
    edit_bench(root, lambda d: d['criteria'][1].pop('id'))
    with pytest.raises(ev.BenchmarkError, match='benchmark smoke criterion 2 has no id'):
        ev.load_benchmark('smoke', root)
    assert ev.main(['run', '--benchmark', 'smoke', '--eval-root', str(root)]) == 2


def test_duplicate_criterion_id_is_rejected(root):
    edit_bench(root, lambda d: d['criteria'][1].update(id='AC1'))
    with pytest.raises(ev.BenchmarkError, match='criterion id AC1 appears twice'):
        ev.load_benchmark('smoke', root)


def test_missing_heldout_test_is_rejected_with_reason(root):
    shutil.rmtree(root / 'heldout' / 'smoke')
    with pytest.raises(ev.BenchmarkError, match=r'benchmark smoke has no held-out test \(heldout/smoke/\*\.json\)'):
        ev.load_benchmark('smoke', root)


@pytest.mark.parametrize('change, reason', [
    (lambda d: d.update(criteria=[]), 'has no acceptance criteria'),
    (lambda d: d.update(requirement='../x.md'), 'path must be relative'),
    (lambda d: d.update(requirement='nope.md'), 'requirement file missing'),
    (lambda d: d.update(expected=['missing.txt']), 'expected fixture missing'),
    (lambda d: d.update(id='other'), 'does not match its folder'),
    (lambda d: d['owner_answers'][0].pop('answer'), 'owner answer 1 has no answer'),
])
def test_invalid_benchmarks_name_the_reason(root, change, reason):
    edit_bench(root, change)
    with pytest.raises(ev.BenchmarkError, match=reason):
        ev.load_benchmark('smoke', root)


def test_heldout_naming_unknown_criterion_is_rejected(root):
    p = root / 'heldout' / 'smoke' / 'H2.json'
    data = json.loads(p.read_text(encoding='utf-8'))
    data['criteria'] = ['AC9']
    p.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ev.BenchmarkError, match="held-out H2 names unknown criterion 'AC9'"):
        ev.load_benchmark('smoke', root)


def test_rubric_missing_dimension_is_rejected(root):
    p = root / 'rubric.json'
    data = json.loads(p.read_text(encoding='utf-8'))
    data['dimensions'] = [d for d in data['dimensions'] if d['id'] != 'cost']
    p.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ev.BenchmarkError, match='rubric is missing dimension\\(s\\): cost'):
        ev.load_rubric(root)


def test_rerun_on_unchanged_input_rewrites_the_same_result(root, tmp_path):
    out = tmp_path / 'out'
    _, first = ev.run('smoke', 'fake', root, results_dir=out)
    before = first.read_bytes()
    _, second = ev.run('smoke', 'fake', root, results_dir=out)
    assert second == first and second.read_bytes() == before
    assert [p.name for p in out.iterdir()] == ['smoke-fake.json']


def test_input_hash_changes_when_heldout_changes(root, tmp_path):
    r1, _ = ev.run('smoke', 'fake', root, results_dir=tmp_path)
    p = root / 'heldout' / 'smoke' / 'H2.json'
    p.write_text(p.read_text(encoding='utf-8').replace('Negative case', 'Negative'), encoding='utf-8')
    r2, _ = ev.run('smoke', 'fake', root, results_dir=tmp_path)
    assert r1['input_hash'] != r2['input_hash']


def test_cli_run_writes_result_and_prints_summary(root, capsys):
    assert ev.main(['run', '--benchmark', 'smoke', '--runner', 'fake', '--eval-root', str(root)]) == 0
    out = capsys.readouterr().out
    assert 'smoke-fake: benchmark smoke' in out and 'evidence E0' in out and 'AC1: passed' in out
    assert (root / 'results' / 'smoke-fake.json').is_file()


def test_unknown_benchmark_is_an_error(root, capsys):
    assert ev.main(['run', '--benchmark', 'nope', '--eval-root', str(root)]) == 2
    assert 'benchmark nope: file missing' in capsys.readouterr().err
