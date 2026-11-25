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


GOOD_PR = dict(number=7, state='open', draft=False, merged=False, head_sha='s1', base='main', author='runner')
GOOD_CHECKS = [dict(name='tests', state='success')]
GOOD_REVIEWS = [dict(user='sup', state='APPROVED', commit_id='s1')]


def gate(pr=None, checks=None, reviews=None, policy=POLICY, merger='sup', files=('a.py',), t=None):
    return g.merge_gate(t or {}, dict(GOOD_PR, **(pr or {})), GOOD_CHECKS if checks is None else checks,
                        GOOD_REVIEWS if reviews is None else reviews, policy, merger, files)


def test_merge_gate_table():
    ok = gate()
    assert ok['verdict'] == 'merge' and ok['reasons'] == [] and ok['sha'] == 's1' and ok['method'] == 'squash'
    assert ok['evidence']['approvals'] == ['sup']
    cases = [
        (dict(pr=dict(state='closed')), 'not open'),
        (dict(pr=dict(draft=True)), 'not open'),
        (dict(pr=dict(merged=True)), 'not open'),
        (dict(t=dict(merged_at='2026-01-01')), 'not open'),
        (dict(pr=dict(base='dev')), 'targets'),
        (dict(checks=[]), 'no CI checks'),
        (dict(checks=[dict(name='tests', state='pending')]), 'not green'),
        (dict(checks=GOOD_CHECKS + [dict(name='lint', state='failure')]), 'lint'),
        (dict(reviews=[]), 'approving review'),
        (dict(reviews=[dict(user='runner', state='APPROVED', commit_id='s1')]), 'approving review'),
        (dict(reviews=[dict(user='sup', state='APPROVED', commit_id='old')]), 'approving review'),
        (dict(reviews=GOOD_REVIEWS + [dict(user='sup', state='CHANGES_REQUESTED', commit_id='s1')]),
         'approving review'),
        (dict(files=['ok.py', '.github/workflows/ci.yml']), 'restricted'),
        (dict(merger='runner'), 'different identity'),
        (dict(merger=''), 'different identity'),
        (dict(policy=None), 'no valid supervisor policy'),
        (dict(pr=dict(head_sha='')), 'no head SHA'),
    ]
    for kw, word in cases:
        out = gate(**kw)
        assert out['verdict'] == 'refuse', kw
        assert any(word in r for r in out['reasons']), (kw, out['reasons'])


def test_merge_gate_lists_every_reason_and_policy_keys():
    out = gate(pr=dict(base='dev'), checks=[], reviews=[], merger='runner')
    assert len(out['reasons']) == 4
    none = dict(POLICY, merge=dict(review='none', method='rebase'))
    out = gate(reviews=[], policy=none)
    assert out['verdict'] == 'merge' and out['method'] == 'rebase'
    assert out['evidence']['files'] == 1


def test_merge_policy_keys_validated():
    good, err = g.parse_policy(json.dumps(dict(POLICY, merge=dict(review='none'))))
    assert err is None and good['merge'] == dict(review='none')
    for bad in (dict(review='maybe'), dict(method='fast'), dict(other=1), 'x'):
        assert g.parse_policy(json.dumps(dict(POLICY, merge=bad)))[0] is None


def ot(number, priority=None, depends_on=(), created_at='2026-01-01T00:00:00Z', status='open'):
    return dict(number=number, priority=priority, depends_on=list(depends_on), created_at=created_at, status=status)


def numbers(result):
    return [o['number'] for o in result]


def test_order_never_crosses_priority():
    # a task that unblocks many others and is the oldest still sits behind a more urgent (lower) priority
    tasks = [ot(1, priority=2, created_at='2026-01-01'), ot(2, priority=2, depends_on=[1]), ot(3, priority=2, depends_on=[1]),
             ot(4, priority=1, created_at='2026-03-01'), ot(5, created_at='2025-01-01'), ot(6, depends_on=[5])]
    assert numbers(g.order(tasks)) == [4, 1, 2, 3, 5, 6]          # priority 1, then 2, then none last
    assert [o['rank'] for o in g.order(tasks)] == [1, 2, 3, 4, 5, 6]


def test_order_unblockers_then_age_then_number():
    tasks = [ot(1, created_at='2026-01-01'), ot(2, created_at='2026-02-01'), ot(3, created_at='2026-03-01'),
             ot(4, depends_on=[3], created_at='2026-04-01'), ot(5, depends_on=[3, 2], created_at='2026-05-01'),
             ot(6, created_at='2026-01-01')]
    result = g.order(tasks)
    assert numbers(result) == [3, 2, 1, 6, 4, 5]
    assert 'unblocks 2 tasks' in result[0]['reason'] and 'unblocks 1 task,' in result[1]['reason']


def test_order_leaves_out_closed_tasks_and_ignores_them_as_blocked():
    tasks = [ot(1, status='done'), ot(2, status='rejected', depends_on=[3]), ot(3), ot(4, depends_on=[1, 99, 4])]
    assert numbers(g.order(tasks)) == [3, 4]
    assert 'unblocks' not in g.order(tasks)[0]['reason'] and g.order([]) == [] and g.order(None) == []
