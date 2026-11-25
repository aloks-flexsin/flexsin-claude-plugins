"""Supervisor policy loader and plan gate (bin/autogates.py). Pure code with an injected reader."""
import json

from tests.auto_runner.test_runner import ar

g = ar._load('autorunner_gates', 'autogates.py')

POLICY = dict(base='main', restricted_paths=['.github/', 'migrations/**', '*.env', 'CLAUDE.md'])
ITEMS = ("- [ ] **X1 Add thing.** Add auto-runner/bin/thing.py.\n  *Acceptance:* tests/test_thing.py passes.\n"
         "  *Slice:* 1 Thing\n")
PLAN = "# Plan\n\n## Queue items\n" + ITEMS + "\n## Needs a person\nNone.\n"


def task(**kw):
    t = dict(status='plan_review', plan=PLAN, items=ITEMS)
    t.update(kw)
    return t


def reader(text, sha='abc123'):
    calls = []

    def read(ref, path):
        calls.append((ref, path))
        return None if text is None else (text, sha)
    read.calls = calls
    return read


def test_policy_read_from_base_with_sha():
    r = reader(json.dumps(POLICY))
    out = g.load_policy(r, 'origin/main')
    assert out == dict(policy=POLICY, sha='abc123', error=None)
    assert r.calls == [('origin/main', '.claude/supervisor-policy.json')]


def test_missing_policy_leaves_plan_for_a_person():
    out = g.load_policy(reader(None), 'origin/main')
    assert out['policy'] is None and 'missing' in out['error']
    assert g.plan_gate(task(), out['policy'])['verdict'] == 'person'


def test_invalid_policies_are_refused():
    for bad in ('{', '[]', '{"base": "main"}', '{"base": "", "restricted_paths": []}',
                '{"base": "main", "restricted_paths": "x"}', '{"base": "main", "restricted_paths": [1]}',
                '{"base": "main", "restricted_paths": [], "extra": 1}'):
        out = g.load_policy(reader(bad), 'main')
        assert out['policy'] is None and out['error'], bad
    assert g.load_policy(lambda ref, path: 1 / 0, 'main')['policy'] is None


def test_all_clear_approves():
    assert g.plan_gate(task(), POLICY) == dict(verdict='approve', reasons=[])


def test_restricted_path_named_in_plan():
    out = g.plan_gate(task(plan=PLAN.replace('auto-runner/bin/thing.py', '.github/workflows/tests.yml')), POLICY)
    assert out['verdict'] == 'changes' and '.github/workflows/tests.yml' in out['reasons'][0]
    out = g.plan_gate(task(items=ITEMS.replace('auto-runner/bin/thing.py', 'migrations/001.sql')), POLICY)
    assert out['verdict'] == 'changes'
    out = g.plan_gate(task(items=ITEMS.replace('auto-runner/bin/thing.py', 'prod.env')), POLICY)
    assert out['verdict'] == 'changes'


def test_unrestricted_paths_pass():
    items = ITEMS.replace('auto-runner/bin/thing.py', 'docs/migrations-note.md')
    assert g.plan_gate(task(items=items), POLICY)['verdict'] == 'approve'


def test_needs_a_person_not_none():
    out = g.plan_gate(task(plan=PLAN.replace('None.', '- Which colour?')), POLICY)
    assert out['verdict'] == 'person' and 'Needs a person' in out['reasons'][0]
    assert g.plan_gate(task(plan=PLAN.split('## Needs')[0]), POLICY)['verdict'] == 'person'


def test_item_without_acceptance():
    items = ITEMS.replace('  *Acceptance:* tests/test_thing.py passes.\n', '')
    out = g.plan_gate(task(items=items), POLICY)
    assert out['verdict'] == 'changes' and 'X1 has no Acceptance' in out['reasons'][0]


def test_no_items_and_wrong_status():
    assert g.plan_gate(task(items=''), POLICY)['verdict'] == 'changes'
    assert g.plan_gate(task(status='building'), POLICY)['verdict'] == 'person'
