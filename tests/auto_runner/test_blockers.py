"""auto-runner blockers (R8): blocker.v1 / decision.v1 validation and the lifecycle state machine, table-driven.
Every legal move succeeds and appends to history, every other move is refused naming both states, ANSWERED is never
unblocked, re-applying a move with the same key is a no-op, and a corrupt file is reported and left untouched."""
import importlib.util
import json
import sys
from collections import deque
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner'


def _mod(name, file):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / 'bin' / file)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


ab = _mod('autorunner_block_r8', 'autoblock.py')

LEGAL = sorted(ab.TRANSITIONS)
ILLEGAL = sorted((s, t) for s in ab.STATES for t in ab.STATES if (s, t) not in ab.TRANSITIONS)


def _path_to(state):
    """Shortest list of states from OPEN to `state` through legal moves."""
    seen, queue = {'OPEN': ['OPEN']}, deque(['OPEN'])
    while queue:
        cur = queue.popleft()
        for (s, t) in ab.TRANSITIONS:
            if s == cur and t not in seen:
                seen[t] = seen[cur] + [t]
                queue.append(t)
    return seen[state]


def _blocker(**over):
    fields = dict(id='B-test1', project='demo', task='R8', run='20261029-1718', agent_role='implementer',
                  stage='G1', question='Can a manager approve their own leave?', why_needed='The approval rule depends on it.',
                  prevents='The approval screen.', impact_if_delayed='R9 waits.', impact_if_wrong='Wrong approvals.',
                  owner_role='product owner', options=[{'label': 'No', 'tradeoffs': 'Needs a second approver.'}])
    fields.update(over)
    return ab.new_blocker(**fields)


def _at(store, state):
    b = store.create(_blocker())
    for i, s in enumerate(_path_to(state)[1:]):
        b = store.transition(b['id'], s, key=f'setup-{i}')
    assert b['state'] == state
    return b


def test_every_state_is_reachable_from_open():
    for s in ab.STATES:
        assert _path_to(s)[0] == 'OPEN'


def test_final_states_have_no_way_out():
    for s in ab.FINAL:
        assert not [t for (f, t) in ab.TRANSITIONS if f == s]


@pytest.mark.parametrize('src,dst', LEGAL)
def test_legal_transition_succeeds_and_appends_history(tmp_path, src, dst):
    store = ab.Store(tmp_path)
    before = _at(store, src)
    after = store.transition(before['id'], dst, key='under-test', reason='because')
    assert after['state'] == dst
    assert after['history'][:len(before['history'])] == before['history']
    last = after['history'][-1]
    assert (last['from'], last['to'], last['key'], last['by']) == (src, dst, 'under-test', ab.TRANSITIONS[(src, dst)])
    assert store.load(before['id']) == after


@pytest.mark.parametrize('src,dst', ILLEGAL)
def test_illegal_transition_is_rejected_naming_both_states(tmp_path, src, dst):
    store = ab.Store(tmp_path)
    before = _at(store, src)
    with pytest.raises(ab.IllegalTransition) as e:
        store.transition(before['id'], dst, key='under-test')
    assert src in str(e.value) and dst in str(e.value)
    assert store.load(before['id']) == before


def test_unknown_state_is_illegal(tmp_path):
    store = ab.Store(tmp_path)
    b = store.create(_blocker())
    with pytest.raises(ab.IllegalTransition, match='OPEN -> DONE'):
        store.transition(b['id'], 'DONE', key='k')


@pytest.mark.parametrize('state', ab.STATES)
def test_only_resumed_and_closed_are_unblocked(state):
    assert ab.is_unblocked(state) == (state in ('RESUMED', 'CLOSED'))


def test_answered_is_never_unblocked(tmp_path):
    store = ab.Store(tmp_path)
    b = _at(store, 'ANSWERED')
    assert not ab.is_unblocked(b['state'])
    for s in ('PENDING_DELIVERY', 'DELIVERED', 'ACKNOWLEDGED', 'RESUMING'):
        assert not ab.is_unblocked(s)


def test_same_key_reapplied_is_a_noop(tmp_path):
    store = ab.Store(tmp_path)
    b = store.create(_blocker())
    once = store.transition(b['id'], 'ANSWERED', key='decision-1')
    raw = store.path(b['id']).read_bytes()
    again = store.transition(b['id'], 'ANSWERED', key='decision-1')
    assert again == once and len(again['history']) == 2
    assert store.path(b['id']).read_bytes() == raw


def test_same_key_reused_for_a_different_move_is_refused(tmp_path):
    store = ab.Store(tmp_path)
    b = store.create(_blocker())
    store.transition(b['id'], 'ANSWERED', key='k1')
    with pytest.raises(ValueError, match='k1'):
        store.transition(b['id'], 'PENDING_DELIVERY', key='k1')


def test_transition_needs_a_key(tmp_path):
    store = ab.Store(tmp_path)
    b = store.create(_blocker())
    with pytest.raises(ValueError, match='key'):
        store.transition(b['id'], 'ANSWERED', key='')


def test_create_is_idempotent_and_refuses_a_different_key(tmp_path):
    store = ab.Store(tmp_path)
    first = store.create(_blocker(idempotency_key='raise-1'))
    assert store.create(_blocker(idempotency_key='raise-1')) == first
    with pytest.raises(ValueError, match='already exists'):
        store.create(_blocker(idempotency_key='raise-2'))
    assert first['state'] == 'OPEN' and first['history'][0]['to'] == 'OPEN'


@pytest.mark.parametrize('content', ['{not json', '[]', json.dumps({'schema': 'blocker.v1', 'id': 'B-test1'})])
def test_corrupt_file_is_reported_not_overwritten(tmp_path, content):
    store = ab.Store(tmp_path)
    store.dir.mkdir(parents=True)
    p = store.path('B-test1')
    p.write_text(content, encoding='utf-8')
    with pytest.raises(ab.CorruptBlocker, match='B-test1'):
        store.load('B-test1')
    with pytest.raises(ab.CorruptBlocker):
        store.transition('B-test1', 'ANSWERED', key='k')
    with pytest.raises(ab.CorruptBlocker):
        store.create(_blocker())
    blockers, problems = store.all()
    assert blockers == [] and len(problems) == 1 and 'B-test1' in problems[0]
    assert p.read_text(encoding='utf-8') == content


def test_writes_leave_no_temp_files(tmp_path):
    store = ab.Store(tmp_path)
    _at(store, 'CLOSED')
    assert [p.name for p in store.dir.iterdir()] == ['B-test1.json']


@pytest.mark.parametrize('over,needle', [
    ({'class': 'B9'}, 'class'),
    ({'question': ''}, 'question'),
    ({'blocking_artifact': {'path': 'spec.md'}}, 'blocking_artifact'),
    ({'options': [{'label': 'x'}]}, 'options'),
    ({'due': 'tomorrow'}, 'due'),
    ({'id': '../escape'}, 'id'),
    ({'extra': 1}, 'unknown field: extra'),
])
def test_invalid_blocker_is_rejected_by_name(tmp_path, over, needle):
    b = _blocker(**over)
    assert any(needle in e for e in ab.validate_blocker(b))
    with pytest.raises(ValueError):
        ab.Store(tmp_path).create(b)


def test_valid_blocker_and_missing_field():
    b = _blocker(blocking_artifact={'path': 'docs/spec.md', 'hash': 'abc'}, due='2026-11-05T00:00:00+00:00')
    assert ab.validate_blocker(b) == []
    del b['owner_role']
    assert ab.validate_blocker(b) == ['missing field: owner_role']


RAISED = {'question': 'Can a manager approve their own leave?', 'why_needed': 'The approval rule depends on it.',
          'prevents': 'The approval screen.', 'impact_if_delayed': 'R9 waits.', 'impact_if_wrong': 'Wrong approvals.',
          'owner_role': 'product owner', 'class': 'B3', 'options': [{'label': 'No', 'tradeoffs': 'Needs a second approver.'}],
          'recommendation': 'No'}


def _drop(folder, name, content):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(content if isinstance(content, str) else json.dumps(content), encoding='utf-8')


def test_ingest_stores_a_valid_file_open_with_runner_fields(tmp_path):
    new = tmp_path / 'wt' / 'new'
    _drop(new, 'q.json', {**RAISED, 'id': 'B-mine', 'state': 'CLOSED', 'task': 'Z9'})      # runner-owned fields are replaced
    [r] = ab.ingest([new], tmp_path / 'auto', 'R9', 'run-1', 'demo')
    assert r['errors'] == [] and r['id'].startswith('B-') and r['id'] != 'B-mine'
    b = ab.Store(tmp_path / 'auto').load(r['id'])
    assert (b['state'], b['task'], b['run'], b['project'], b['stage'], b['agent_role']) == ('OPEN', 'R9', 'run-1', 'demo', 'R9', 'implementer')
    assert b['history'][0]['to'] == 'OPEN' and not list(new.iterdir())


def test_ingest_same_file_twice_is_one_blocker(tmp_path):
    new, auto = tmp_path / 'new', tmp_path / 'auto'
    _drop(new, 'q.json', RAISED)
    first = ab.ingest([new], auto, 'R9', 'run-1', 'demo')[0]['id']
    _drop(new, 'q.json', RAISED)
    assert ab.ingest([new], auto, 'R9', 'run-2', 'demo')[0]['id'] == first
    assert len(ab.Store(auto).all()[0]) == 1
    _drop(new, 'q.json', RAISED)
    assert ab.ingest([new], auto, 'R10', 'run-2', 'demo')[0]['id'] != first                 # another item: its own blocker


@pytest.mark.parametrize('content,reason', [
    ('{oops', 'not valid JSON'),
    ('[1, 2]', 'one JSON object'),
    ({**RAISED, 'schema': 'blocker.v0'}, 'schema'),
    ({**RAISED, 'class': 'B9'}, 'class'),
    ({k: v for k, v in RAISED.items() if k != 'why_needed'}, 'why_needed'),
    ({**RAISED, 'mood': 'grumpy'}, 'unknown field: mood'),
])
def test_ingest_rejects_invalid_files_with_reasons_and_keeps_them(tmp_path, content, reason):
    new, auto = tmp_path / 'new', tmp_path / 'auto'
    _drop(new, 'q.json', content)
    raw = (new / 'q.json').read_bytes()
    [r] = ab.ingest([new], auto, 'R9', 'run-1', 'demo', stamp='s1')
    assert r['id'] is None and any(reason in e for e in r['errors'])
    kept = auto / 'blockers' / 'rejected'
    assert (kept / 'q-s1.json').read_bytes() == raw and reason in (kept / 'q-s1.reasons.txt').read_text(encoding='utf-8')
    assert ab.Store(auto).all() == ([], []) and not list(new.iterdir())


def test_ingest_without_a_task_rejects_and_nothing_found_creates_nothing(tmp_path):
    auto = tmp_path / 'auto'
    assert ab.ingest([tmp_path / 'absent'], auto, 'R9', 'run-1', 'demo') == []
    assert not auto.exists()
    _drop(tmp_path / 'new', 'q.json', RAISED)
    [r] = ab.ingest([tmp_path / 'new'], auto, None, 'run-1', 'demo')
    assert r['id'] is None and 'before any iteration' in r['errors'][0]


def test_rejected_names_never_collide(tmp_path):
    new, auto = tmp_path / 'new', tmp_path / 'auto'
    for _ in range(2):
        _drop(new, 'q.json', '{bad')
        ab.ingest([new], auto, 'R9', 'run-1', 'demo', stamp='s1')
    assert sorted(p.name for p in (auto / 'blockers' / 'rejected').glob('*.json')) == ['q-s1-2.json', 'q-s1.json']


def test_view_lists_waiting_blockers_only(tmp_path):
    store = ab.Store(tmp_path)
    open_ = store.create(_blocker(id='B-open1'))
    _at(store, 'CLOSED')                                                                      # B-test1, finished
    path = ab.write_view(tmp_path)
    text = path.read_text(encoding='utf-8')
    assert path.name == 'QUESTIONS.md' and 'Do not edit' in text
    assert 'B-open1' in text and open_['question'] in text and 'Option "No": Needs a second approver.' in text
    assert 'B-test1' not in text
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith('.tmp-')] == []


def test_view_reports_unreadable_files_and_empty_store(tmp_path):
    assert '(no blocker is waiting)' in ab.render_view([])
    assert 'B-bad.json: unreadable' in ab.render_view([], ['B-bad.json: unreadable (JSONDecodeError)'])


def test_list_command_filters_by_state(tmp_path, capsys):
    store = ab.Store(tmp_path)
    store.create(_blocker(id='B-one'))
    store.transition(store.create(_blocker(id='B-two'))['id'], 'ANSWERED', key='k')
    assert ab.main(['list', '--auto', str(tmp_path)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [ln.split('\t')[:3] for ln in out] == [['B-one', 'OPEN', 'R8'], ['B-two', 'ANSWERED', 'R8']]
    assert ab.main(['list', '--auto', str(tmp_path), '--state', 'ANSWERED']) == 0
    assert capsys.readouterr().out.startswith('B-two\tANSWERED')
    (store.dir / 'B-bad.json').write_text('{', encoding='utf-8')
    assert ab.main(['list', '--auto', str(tmp_path)]) == 1
    assert 'unreadable' in capsys.readouterr().err
    assert ab.main(['view', '--auto', str(tmp_path)]) == 0
    assert 'QUESTIONS.md' in capsys.readouterr().out


def test_decision_schema():
    d = ab.new_decision('B-test1', 1, 'alok', 'product owner', option='No', context='Policy 4.2', applies_to='spec@3')
    assert ab.validate_decision(d) == [] and d['answer'] == {'option': 'No'}
    with pytest.raises(ValueError, match='answer'):
        ab.new_decision('B-test1', 1, 'alok', 'product owner')
    with pytest.raises(ValueError, match='version'):
        ab.new_decision('B-test1', 0, 'alok', 'product owner', text='No')
    assert 'unknown field: x' in ab.validate_decision(dict(d, x=1))
