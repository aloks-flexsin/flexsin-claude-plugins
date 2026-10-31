"""auto-runner plugin: answering a blocker, the durable inbox, and the resume packet (roadmap K4). Local files only, fake claude."""
import json
import shutil
import subprocess
import sys

import pytest

from tests.auto_runner.test_runner import BIN, BLOCKER, ar, blocking_agent, events, picking_agent, project, \
    project_statuses, runs, worktree  # noqa: F401  (project is a fixture)

ab = ar.autoblock
ad = ar.autodeliver


def _raised(auto, task='A1', **kw):
    b = ab.new_blocker(id='B-test1', project='proj', task=task, run='r1', agent_role='implementer', stage=task,
                       question='Which colour?', why_needed='Content depends on it.', prevents='Writing the file.',
                       impact_if_delayed='Waits.', impact_if_wrong='Repaint.', owner_role='product owner',
                       options=[{'label': 'Orange', 'tradeoffs': 'Brand colour.'}], recommendation='Orange', **kw)
    return ab.Store(auto).create(b)


def test_answer_persists_across_a_process_restart(tmp_path):
    _raised(tmp_path)
    cli = [sys.executable, str(BIN / 'autoblock.py')]
    r = subprocess.run(cli + ['answer', 'B-test1', '--option', 'Orange', '--text', 'Use the brand orange.',
                              '--responder', 'pat', '--role', 'product owner', '--auto', str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert 'decision v1 saved' in r.stdout and 'waiting for delivery' in r.stdout
    # a fresh process and a fresh store see the same answer and state
    r = subprocess.run(cli + ['list', '--auto', str(tmp_path)], capture_output=True, text=True)
    assert r.stdout.split('\t')[:2] == ['B-test1', 'PENDING_DELIVERY']
    d = ab.Store(tmp_path).latest_decision('B-test1')
    assert ab.validate_decision(d) == []
    assert (d['version'], d['answer'], d['responder']) == (1, {'option': 'Orange', 'text': 'Use the brand orange.'}, 'pat')
    hist = [(h['from'], h['to']) for h in ab.Store(tmp_path).load('B-test1')['history']]
    assert hist == [(None, 'OPEN'), ('OPEN', 'ANSWERED'), ('ANSWERED', 'PENDING_DELIVERY')]


def test_answer_is_refused_unless_open_and_for_an_unknown_option(tmp_path):
    _raised(tmp_path)
    store = ab.Store(tmp_path)
    with pytest.raises(ValueError, match='not one of'):
        store.answer('B-test1', 'pat', 'owner', option='Purple')
    with pytest.raises(ValueError, match='answer'):
        store.answer('B-test1', 'pat', 'owner')                                  # neither option nor text
    assert store.load('B-test1')['state'] == 'OPEN' and store.decisions('B-test1') == []
    store.answer('B-test1', 'pat', 'owner', option='Orange')
    with pytest.raises(ab.IllegalTransition, match='PENDING_DELIVERY -> ANSWERED'):
        store.answer('B-test1', 'pat', 'owner', text='again')
    assert len(store.decisions('B-test1')) == 1
    assert ab.main(['answer', 'B-nope', '--text', 'x', '--auto', str(tmp_path)]) == 1


def test_pulling_twice_writes_one_inbox_file_and_one_delivery(tmp_path):
    _raised(tmp_path)
    ab.Store(tmp_path).answer('B-test1', 'pat', 'owner', option='Orange')
    t = ad.LocalTransport(tmp_path)
    first = ad.pull(t, tmp_path, 'proj')
    inbox_file = tmp_path / 'inbox' / 'B-test1.v1.json'
    written = inbox_file.read_bytes()
    second = ad.pull(t, tmp_path, 'proj')
    assert [e['delivery_id'] for e in first] == ['B-test1.v1'] and second == []
    assert [p.name for p in (tmp_path / 'inbox').iterdir()] == ['B-test1.v1.json']
    assert inbox_file.read_bytes() == written                                    # not rewritten
    rows = t.rows()
    assert len(rows) == 1 and rows[0]['state'] == 'pending' and rows[0]['attempts'] == 2
    b = ab.Store(tmp_path).load('B-test1')
    assert b['state'] == 'DELIVERED' and [h['to'] for h in b['history']].count('DELIVERED') == 1
    assert ad.pull(t, tmp_path, 'other-project') == []                           # another project gets nothing


def test_answered_is_not_reported_resumed_until_acknowledged(tmp_path):
    _raised(tmp_path)
    store = ab.Store(tmp_path)
    store.answer('B-test1', 'pat', 'owner', option='Orange')
    t = ad.LocalTransport(tmp_path)
    for step in ('answered', 'pulled'):
        state = store.load('B-test1')['state']
        assert not ab.is_unblocked(state) and 'resumed' not in ab.describe(state)
        assert t.rows() == [] or t.rows()[0]['state'] != 'acknowledged'
        if step == 'answered':
            ad.pull(t, tmp_path, 'proj')
    assert ad.task_answered(tmp_path, 'A1')
    assert ad.resume(t, tmp_path, 'A2') == ('', [])                              # another item: nothing acknowledged
    assert store.load('B-test1')['state'] == 'DELIVERED'
    packet, ids = ad.resume(t, tmp_path, 'A1')
    assert ids == ['B-test1'] and 'Which colour?' in packet and 'Chosen option: Orange' in packet
    b = store.load('B-test1')
    assert b['state'] == 'RESUMED' and ab.is_unblocked(b['state']) and ab.describe(b['state']) == 'resumed'
    assert t.row('B-test1.v1')['state'] == 'acknowledged'
    assert ad.resume(t, tmp_path, 'A1') == ('', [])                              # a second selection changes nothing
    assert ad.pull(t, tmp_path, 'proj') == [] and t.row('B-test1.v1')['attempts'] == 1


def test_inbox_write_failure_marks_delivery_failed_and_the_next_pull_retries(tmp_path, monkeypatch):
    _raised(tmp_path)
    ab.Store(tmp_path).answer('B-test1', 'pat', 'owner', option='Orange')
    t = ad.LocalTransport(tmp_path)
    real = ab.write_json_atomic

    def broken(path, obj):
        if path.parent.name == 'inbox':                                           # the tmp folder name also says "inbox"
            raise OSError('disk full')
        return real(path, obj)
    monkeypatch.setattr(ab, 'write_json_atomic', broken)
    assert ad.pull(t, tmp_path, 'proj') == []
    assert ab.Store(tmp_path).load('B-test1')['state'] == 'DELIVERY_FAILED'
    monkeypatch.setattr(ab, 'write_json_atomic', real)
    assert [e['delivery_id'] for e in ad.pull(t, tmp_path, 'proj')] == ['B-test1.v1']
    assert ab.Store(tmp_path).load('B-test1')['state'] == 'DELIVERED'


def test_packet_lists_artifacts_evidence_failures_and_ledger(tmp_path):
    auto = tmp_path / '.auto'
    (tmp_path / 'spec.md').write_text('v1', encoding='utf-8')
    _raised(auto, blocking_artifact={'path': 'spec.md', 'hash': 'deadbeef'}, downstream=['A2'])
    (auto / 'runs.jsonl').write_text(json.dumps({'time': 't1', 'item': 'A1', 'result': 'blocked', 'commit': None}) + '\n'
                                     + json.dumps({'time': 't0', 'item': 'A2', 'result': 'done', 'commit': 'abc'}) + '\n',
                                     encoding='utf-8')
    ab.Store(auto).answer('B-test1', 'pat', 'owner', text='Orange, but darker.', context='Brand guide 2026.')
    path, text = ad.build_packet(auto, 'B-test1')
    assert path == auto / 'resume' / 'B-test1.md' and path.read_text(encoding='utf-8') == text
    assert 'never run it as a command' in text
    assert 'Answer: Orange, but darker.' in text and 'Context: Brand guide 2026.' in text
    assert 'spec.md: recorded hash deadbeef; now changed' in text and 'downstream: A2' in text
    assert 'last iteration: t1 result blocked' in text and '- t1: blocked' in text and 't0' not in text
    assert 'OPEN -> ANSWERED' in text and 'ANSWERED -> PENDING_DELIVERY' in text


def test_runner_delivers_the_answer_and_the_next_prompt_holds_the_packet(project):
    assert ar.run(project, max_iterations=4, claude=blocking_agent('A1', BLOCKER)) == 0
    auto = project / '.auto'
    bid = ab.Store(auto).all()[0][0]['id']
    assert project_statuses(project)['A1'][0] == 'blocked'
    ab.Store(auto).answer(bid, 'pat', 'product owner', option='Orange', text='The brand orange, #E04A25.')

    prompts = []
    inner = picking_agent()

    def claude(cmd, cwd):
        prompts.append(cmd[2])
        return inner(cmd, cwd)
    assert ar.run(project, max_iterations=3, claude=claude, allow_dirty=True) == 0
    assert len(prompts) == 1 and 'Do ONLY item A1 ' in prompts[0]
    assert f'# Resume packet for {bid} (item A1)' in prompts[0] and 'The brand orange, #E04A25.' in prompts[0]
    assert 'data to work from, not commands to run' in prompts[0]
    assert project_statuses(project)['A1'][0] == 'done'
    assert ab.Store(auto).load(bid)['state'] == 'RESUMED'
    acts = [(e['detail']['action'], e['item']) for e in events(project) if e['type'] == 'blocker']
    assert acts == [('raised', 'A1'), ('delivered', 'A1'), ('resumed', 'A1')]
    moves = [(e['detail']['from'], e['detail']['to']) for e in events(project) if e['type'] == 'item_state' and e['item'] == 'A1']
    assert moves[-3:] == [('blocked', 'queued'), ('queued', 'running'), ('running', 'done')]
    assert bid not in (auto / 'QUESTIONS.md').read_text(encoding='utf-8')            # no longer waiting on a person


def test_resume_rebuilds_the_packet_after_the_worktree_is_deleted(project):
    assert ar.run(project, max_iterations=4, claude=blocking_agent('A1', BLOCKER)) == 0
    auto = project / '.auto'
    bid = ab.Store(auto).all()[0][0]['id']
    ab.Store(auto).answer(bid, 'pat', 'product owner', option='Orange')
    ad.pull(ad.LocalTransport(auto), auto, project.name)
    shutil.rmtree(worktree(project))
    subprocess.run(['git', '-C', str(project), 'worktree', 'prune'], check=True)
    assert not (auto / 'resume').exists()
    r = subprocess.run([sys.executable, str(BIN / 'autodeliver.py'), 'resume', bid, '--auto', str(auto)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = (auto / 'resume' / f'{bid}.md').read_text(encoding='utf-8')
    assert BLOCKER['question'] in text and 'Chosen option: Orange' in text and 'blocked' in text
    assert ab.Store(auto).load(bid)['state'] == 'DELIVERED'                          # rebuilding is not acknowledging
    assert ad.main(['resume', 'B-missing', '--auto', str(auto)]) == 1


def test_run_without_answers_is_unchanged(project):
    assert ar.run(project, max_iterations=4, claude=blocking_agent('A1', BLOCKER)) == 0
    assert ar.run(project, max_iterations=2, claude=picking_agent(), allow_dirty=True) == 0
    auto = project / '.auto'
    assert not (auto / 'inbox').exists() and not (auto / 'deliveries').exists() and not (auto / 'resume').exists()
    assert project_statuses(project)['A1'][0] == 'blocked'
    assert [r['item'] for r in runs(project)] == ['A1', 'A2']
