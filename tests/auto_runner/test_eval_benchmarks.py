"""auto-runner evaluation benchmarks (R2): every shipped brief loads through the R1 validator and has what a benchmark needs."""
import importlib.util
import json
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


ev = _mod('autorunner_eval_b', 'autoeval.py')

SHAPES = {
    'workflow-approval': 'workflow-heavy internal app',
    'multitenant-saas': 'multi-tenant SaaS',
    'integration-orders': 'integration-heavy app',
    'existing-codebase': 'feature added to an unfamiliar existing codebase',
    'concurrency-bookings': 'concurrency-sensitive rules app (balances and bookings)',
    'ambiguity-decisions': 'ambiguity needing owner decisions',
    'seeded-defects': 'seeded-defect scenario',
}


def test_every_roadmap_benchmark_is_shipped():
    assert set(SHAPES) <= set(ev.list_benchmarks(EVAL))


@pytest.mark.parametrize('bid', ev.list_benchmarks(EVAL))
def test_benchmark_loads_through_validator(bid):
    bench = ev.load_benchmark(bid, EVAL)
    ids = [c['id'] for c in bench['criteria']]
    assert len(ids) == len(set(ids)), 'criterion ids must be unique'
    assert bench['heldout'], 'needs at least one held-out test'
    assert any(c.get('negative') for c in bench['criteria']), 'needs at least one negative case'
    covered = {r for h in bench['heldout'] for r in h['criteria']}
    assert covered == set(ids), f'criteria without a held-out test: {sorted(set(ids) - covered)}'


@pytest.mark.parametrize('bid', sorted(SHAPES))
def test_shape_and_brief(bid):
    bench = ev.load_benchmark(bid, EVAL)
    assert bench['shape'] == SHAPES[bid]
    assert (bench['dir'] / bench['requirement']).read_text(encoding='utf-8').startswith('# Benchmark:')


def test_validate_command_accepts_all():
    assert ev.main(['validate', '--eval-root', str(EVAL)]) == 0


def test_ambiguity_has_an_insufficient_and_an_unanswered_decision():
    bench = ev.load_benchmark('ambiguity-decisions', EVAL)
    assert any(a['sufficient'] is False for a in bench['owner_answers'])
    owner = ev.OwnerSimulator(bench['owner_answers'])
    owner.ask('Who approves the CFO expense claims?')
    owner.ask('How long are receipts kept?')
    assert owner.transcript[0]['answer'] and owner.transcript[0]['sufficient'] is False
    assert owner.transcript[1]['answer'] is None


def test_seeded_defects_are_not_revealed_to_the_agent():
    """The defects are described only in held-out tests; the brief and criteria the agent sees never name them."""
    bench = ev.load_benchmark('seeded-defects', EVAL)
    view = json.dumps(ev.agent_view(bench)) + (bench['dir'] / bench['requirement']).read_text(encoding='utf-8')
    for secret in ('/api/v0/timesheets', '0007', 'NOT NULL', 'local time', 'Seeded defect'):
        assert secret not in view
    assert sum('Seeded defect' in h['description'] for h in bench['heldout']) == 3


@pytest.mark.parametrize('bid', sorted(SHAPES))
def test_descriptions_never_count_as_passes(bid, tmp_path):
    """Briefs only: held-out tests are descriptions, so a fake run that approves everything scores nothing as passed."""
    result, _ = ev.run(bid, 'fake', EVAL, results_dir=tmp_path)
    assert {h['status'] for h in result['heldout']} == {'not_run'}
    assert {c['status'] for c in result['criteria'].values()} == {'untested'}
    assert result['scores']['functional_completeness']['value'] is None
    assert result['scores']['incorrect_approval_rate']['value'] is None
