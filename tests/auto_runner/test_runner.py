"""auto-runner plugin: the Python runner on a scratch git repo with a fake claude command (no network, no real agent)."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autorunner_run_t', BIN / 'auto_run.py')
ar = importlib.util.module_from_spec(spec)
sys.modules['autorunner_run_t'] = ar
spec.loader.exec_module(ar)

PY = f'"{sys.executable}"'
GREEN = f'{PY} -c "import sys; sys.exit(0)"'
RED = f'{PY} -c "import sys; sys.exit(1)"'

QUEUE = """# Queue

- [ ] **A1 First thing.** Build the first thing in a small file, with enough words to pass any length rule we might have.
  *Acceptance:* the file exists.

- [ ] **A2 Second thing.** Build the second thing in a small file, with enough words to pass any length rule we might have.
  *Acceptance:* the file exists.

## Needs a person
- nothing
"""


def sh(cwd, *args):
    subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def project(tmp_path):
    p = tmp_path / 'proj'
    p.mkdir()
    sh(p, 'init', '-q', '-b', 'main')
    sh(p, 'config', 'user.email', 't@example.com')
    sh(p, 'config', 'user.name', 'T')
    (p / 'README.md').write_text('x', encoding='utf-8')
    (p / '.claude').mkdir()
    (p / '.claude' / 'auto.json').write_text(json.dumps({'test_command': GREEN}), encoding='utf-8')
    (p / '.claude' / 'settings.json').write_text(
        json.dumps({'permissions': {'deny': ar.autosafe.required_rules('main')}}), encoding='utf-8')
    (p / '.auto').mkdir()
    (p / '.auto' / 'queue.md').write_text(QUEUE, encoding='utf-8')
    sh(p, 'add', 'README.md', '.claude/auto.json', '.claude/settings.json')
    sh(p, 'commit', '-q', '-m', 'init')
    return p


def set_cfg(p, **kw):
    cfg = {'test_command': GREEN}
    cfg.update(kw)
    (p / '.claude' / 'auto.json').write_text(json.dumps(cfg), encoding='utf-8')
    if (cfg.get('changelog') or {}).get('enabled'):   # the changelog also needs the git tag deny rules (Q9)
        (p / '.claude' / 'settings.json').write_text(
            json.dumps({'permissions': {'deny': ar.autosafe.required_rules('main', changelog=True)}}), encoding='utf-8')
    sh(p, 'add', '.claude/auto.json', '.claude/settings.json')
    sh(p, 'commit', '-q', '-m', 'cfg')


def worktree(p):
    return p.parent / (p.name + '-auto')


def fake_agent(output='done', calls=None, commit=True, tick=True, leave_dirty=False):
    """Does what the prompt asks for the first open item: a file, a ticked box, one commit."""
    def claude(cmd, cwd):
        cwd = Path(cwd)
        if calls is not None:
            calls.append(cmd)
        q = cwd / '.auto' / 'queue.md'
        lines = q.read_text(encoding='utf-8').splitlines()
        for n, line in enumerate(lines):
            if line.startswith('- [ ] **'):
                item = line.split('**')[1].split()[0]
                if tick:
                    lines[n] = line.replace('- [ ]', '- [x]', 1)
                break
        else:
            return 0, output
        q.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        (cwd / f'{item}.txt').write_text('made', encoding='utf-8')
        if commit:
            sh(cwd, 'add', f'{item}.txt')
            sh(cwd, '-c', 'user.email=t@example.com', '-c', 'user.name=T', 'commit', '-q', '-m', f'{item}')
        if leave_dirty:
            (cwd / 'stray.txt').write_text('left behind', encoding='utf-8')
        return 0, output
    return claude


def runs(p):
    f = p / '.auto' / 'runs.jsonl'
    return [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines()] if f.exists() else []


def test_one_item_done_and_committed(project):
    code = ar.run(project, max_iterations=1, claude=fake_agent())
    assert code == 0
    wt = worktree(project)
    assert (wt / 'A1.txt').exists() and not (wt / 'A2.txt').exists()
    log = subprocess.run(['git', '-C', str(wt), 'log', '--oneline'], capture_output=True, text=True).stdout
    assert 'A1' in log
    r = runs(project)
    assert len(r) == 1 and r[0]['item'] == 'A1' and r[0]['result'] == 'done' and r[0]['commit']
    assert not (project / '.auto' / 'lock').exists()
    assert (project / 'A1.txt').exists() is False                        # the project folder itself is untouched


def test_queue_copy_in_project_matches_worktree_and_shows_status(project):
    ar.run(project, max_iterations=1, claude=fake_agent())
    wq = (worktree(project) / '.auto' / 'queue.md').read_text(encoding='utf-8')
    pq = (project / '.auto' / 'queue.md').read_text(encoding='utf-8')
    assert '- [x] **A1' in pq
    strip = lambda t: '\n'.join(x for x in t.splitlines() if '*Status:*' not in x)
    assert strip(pq) == strip(wq)
    queue = ar.queue
    st = {i: (s, d) for i, s, d in queue.statuses(pq)}
    assert st['A1'][0] == 'done' and st['A2'][0] == 'queued'


def test_red_suite_stops_the_run(project):
    set_cfg(project, test_command=RED)
    calls = []
    code = ar.run(project, max_iterations=3, claude=fake_agent(calls=calls))
    assert code == 1 and len(calls) == 1
    assert runs(project)[0]['result'] == 'red'
    assert not (project / '.auto' / 'lock').exists()


def test_stop_file_stops_between_iterations(project):
    calls = []
    inner = fake_agent(calls=calls)

    def claude(cmd, cwd):
        out = inner(cmd, cwd)
        (project / '.auto' / 'STOP').write_text('', encoding='utf-8')
        return out
    assert ar.run(project, max_iterations=5, claude=claude) == 0
    assert len(calls) == 1


def test_second_runner_refused_while_lock_exists(project):
    (project / '.auto' / 'lock').write_text('123', encoding='utf-8')
    with pytest.raises(ar.RunnerError, match='already in progress'):
        ar.run(project, max_iterations=1, claude=fake_agent())
    assert not worktree(project).exists()
    assert (project / '.auto' / 'lock').exists()                          # not ours to remove


def test_dry_run_changes_nothing(project, capsys):
    before = (project / '.auto' / 'queue.md').read_text(encoding='utf-8')
    called = []
    code = ar.run(project, dry_run=True, claude=lambda *a: called.append(a))
    assert code == 0 and not called
    assert not worktree(project).exists()
    assert not (project / '.auto' / 'lock').exists() and not (project / '.auto' / 'runs.jsonl').exists()
    assert (project / '.auto' / 'queue.md').read_text(encoding='utf-8') == before
    assert 'dry run' in capsys.readouterr().out


def test_cost_is_null_without_output_and_read_when_reported(project):
    ar.run(project, max_iterations=1, claude=fake_agent(output='no json here'))
    assert runs(project)[0]['cost_usd'] is None
    ar.run(project, max_iterations=1, claude=fake_agent(output='x\n{"type":"result","total_cost_usd":0.37}'), allow_dirty=True)
    assert runs(project)[1]['cost_usd'] == 0.37
    assert ar.parse_cost('{"total_cost_usd": "lots"}') is None


def test_leftovers_stop_the_run(project):
    code = ar.run(project, max_iterations=3, claude=fake_agent(leave_dirty=True))
    assert code == 1
    assert runs(project)[0]['result'] == 'leftovers'


def test_no_progress_twice_stops(project):
    calls = []

    def idle(cmd, cwd):
        calls.append(cmd)
        return 0, ''
    assert ar.run(project, max_iterations=6, claude=idle) == 1
    assert len(calls) == 2
    assert [r['result'] for r in runs(project)] == ['no_progress', 'no_progress']


def test_check_only_says_unverified_and_skips_tests(project, capsys):
    set_cfg(project, test_command='', check_only=True)
    ran = []
    assert ar.run(project, max_iterations=1, claude=fake_agent(), tests=lambda *a: ran.append(a) or (1, '')) == 0
    assert not ran
    assert 'unverified: no tests' in capsys.readouterr().out
    assert 'unverified: no tests' in (project / '.auto' / 'queue.md').read_text(encoding='utf-8')


def test_uncommitted_project_refused_unless_allowed(project):
    (project / 'dirty.txt').write_text('x', encoding='utf-8')
    with pytest.raises(ar.RunnerError, match='uncommitted'):
        ar.run(project, max_iterations=1, claude=fake_agent())
    assert ar.run(project, max_iterations=1, claude=fake_agent(), allow_dirty=True) == 0


def test_enqueue_adds_between_iterations_and_rejects_bad_requests(project):
    good = project / '.auto' / 'pending' / 'more'
    good.mkdir(parents=True)
    (good / 'items.md').write_text(
        '- [ ] **A3 Third thing.** Build the third thing in a small file, with enough words to pass any length rule we might have.\n'
        '  *Acceptance:* the file exists.\n', encoding='utf-8')
    bad = project / '.auto' / 'pending' / 'bad'
    bad.mkdir()
    (bad / 'items.md').write_text('- [ ] **A1 Duplicate.** short\n', encoding='utf-8')
    assert ar.run(project, max_iterations=1, claude=fake_agent(), enqueue='more', allow_dirty=True) == 0
    pq = (project / '.auto' / 'queue.md').read_text(encoding='utf-8')
    assert '**A3' in pq
    assert list((project / '.auto' / 'applied').iterdir()) and not good.exists()
    # the bad request is only judged when it is applied, and is then set aside
    assert list((project / '.auto' / 'rejected').iterdir())


def test_enqueue_with_bad_request_is_refused_up_front(project, capsys):
    bad = project / '.auto' / 'pending' / 'bad'
    bad.mkdir(parents=True)
    (bad / 'items.md').write_text('- [ ] **A1 Duplicate.** short\n', encoding='utf-8')
    assert ar.run(project, enqueue='bad', claude=fake_agent()) == 1
    assert not worktree(project).exists()


def test_enqueue_waits_while_a_run_holds_the_lock(project):
    good = project / '.auto' / 'pending' / 'more'
    good.mkdir(parents=True)
    (good / 'items.md').write_text(
        '- [ ] **A3 Third thing.** Build the third thing in a small file, with enough words to pass any length rule we might have.\n'
        '  *Acceptance:* the file exists.\n', encoding='utf-8')
    (project / '.auto' / 'lock').write_text('1', encoding='utf-8')
    assert ar.run(project, enqueue='more', claude=fake_agent()) == 0
    assert good.exists() and not worktree(project).exists()


def test_share_dirs_are_linked_into_the_worktree(project):
    (project / 'deps').mkdir()
    (project / 'deps' / 'marker.txt').write_text('shared', encoding='utf-8')
    set_cfg(project, share_dirs=['deps'])
    assert ar.run(project, max_iterations=1, claude=fake_agent()) == 0
    assert (worktree(project) / 'deps' / 'marker.txt').read_text(encoding='utf-8') == 'shared'


def test_prompt_uses_config_not_siva_names(project):
    cfg = {'test_command': 'npm test', 'check_only': False, 'queue_file': '.auto/queue.md', 'rules_files': ['RULES.md']}
    text = ar.build_prompt(cfg, '.auto/queue.md')
    assert 'RULES.md' in text and 'npm test' in text and '.auto/queue.md' in text
    assert 'mission-control' not in text and 'SaleEnable' not in text and 'venv' not in text


def events(p):
    return ar.autoevents.read(p / '.auto')


def scripted_tests(*codes):
    """Fake independent suite: returns the given exit codes in order."""
    seq = list(codes)
    return lambda command, cwd: (seq.pop(0), 'fake suite')


def test_two_item_run_with_red_stop_gives_one_event_per_transition(project):
    code = ar.run(project, max_iterations=5, claude=fake_agent(), tests=scripted_tests(0, 1))
    assert code == 1
    ev = events(project)
    assert all(ar.autoevents.validate(e) == [] for e in ev)
    got = [(e['type'], e['item'], e['detail'].get('from'), e['detail'].get('to') or e['detail'].get('result')) for e in ev]
    assert got == [
        ('run_start', None, None, None),
        ('item_state', 'A1', 'queued', 'running'),
        ('tests', 'A1', None, 'pass'),
        ('item_state', 'A1', 'running', 'done'),
        ('item_state', 'A2', 'queued', 'running'),
        ('tests', 'A2', None, 'fail'),
        ('item_state', 'A2', 'running', 'blocked'),
        ('run_end', None, None, None),
    ]
    assert ev[-1]['detail'] == {'exit': 1, 'reason': 'red'}
    assert {e['evidence_class'] for e in ev if e['type'] == 'tests'} == {'independent_test'}
    assert len({e['id'] for e in ev}) == len(ev) and len({e['run'] for e in ev}) == 1
    assert {e['project'] for e in ev} == {project.name}


def test_event_ledger_is_append_only_across_runs(project):
    ar.run(project, max_iterations=1, claude=fake_agent())
    first = (project / '.auto' / 'events.jsonl').read_text(encoding='utf-8')
    assert ar.run(project, max_iterations=1, claude=fake_agent(), allow_dirty=True) == 0
    second = (project / '.auto' / 'events.jsonl').read_text(encoding='utf-8')
    assert second.startswith(first) and len(second) > len(first)
    ev = events(project)
    assert [e['type'] for e in ev].count('run_start') == 2 and [e['type'] for e in ev].count('run_end') == 2
    assert ev[-1]['detail'] == {'exit': 0, 'reason': 'max_iterations'}


def test_runs_jsonl_keeps_its_old_shape_with_the_ledger(project):
    ar.run(project, max_iterations=5, claude=fake_agent(), tests=scripted_tests(0, 1))
    r = runs(project)
    assert [x['item'] for x in r] == ['A1', 'A2'] and [x['result'] for x in r] == ['done', 'red']
    assert all(set(x) == {'time', 'item', 'result', 'commit', 'duration_s', 'cost_usd'} for x in r)
    assert not any('schema' in x or 'evidence_class' in x for x in r)


def test_dry_run_writes_no_events(project):
    ar.run(project, dry_run=True, claude=fake_agent())
    assert not (project / '.auto' / 'events.jsonl').exists()


def test_event_validation_rejects_bad_events():
    ev = ar.autoevents
    good = ev.make('r1', 'proj', 'item_state', 'A1', {'from': 'queued', 'to': 'running'})
    assert ev.validate(good) == []
    assert any('schema' in e for e in ev.validate({**good, 'schema': 'event.v0'}))
    assert any('type' in e for e in ev.validate({**good, 'type': 'party'}))
    assert any('evidence_class' in e for e in ev.validate({**good, 'evidence_class': 'vibes'}))
    assert any('item' in e for e in ev.validate({**good, 'item': None}))
    assert any('item' in e for e in ev.validate({**good, 'type': 'run_start'}))
    assert any('unknown field: extra' == e for e in ev.validate({**good, 'extra': 1}))
    missing = dict(good)
    del missing['time']
    assert 'missing field: time' in ev.validate(missing)
    with pytest.raises(ValueError):
        ev.make('r1', 'proj', 'tests', None)


def test_agent_command_has_expected_flags(project):
    calls = []
    ar.run(project, max_iterations=1, max_budget_usd=2, claude=fake_agent(calls=calls))
    cmd = calls[0]
    assert cmd[:2] == ['claude', '-p'] and '--permission-mode' in cmd and 'acceptEdits' in cmd
    assert cmd[cmd.index('--max-budget-usd') + 1] == '2'


def test_no_model_flag_when_implementer_is_unset(project):
    calls = []
    set_cfg(project, models={'planner': 'opus'})                     # another role set: still no flag for the implementer
    ar.run(project, max_iterations=1, claude=fake_agent(calls=calls))
    assert '--model' not in calls[0]


@pytest.mark.parametrize('value', ['sonnet', 'claude-opus-5-5'])
def test_implementer_model_is_passed_exactly(project, value):
    calls = []
    set_cfg(project, models={'implementer': value, 'tester': 'haiku'})
    ar.run(project, max_iterations=1, claude=fake_agent(calls=calls))
    cmd = calls[0]
    assert cmd.count('--model') == 1 and cmd[cmd.index('--model') + 1] == value


def test_inherit_means_no_model_flag(project, capsys):
    calls = []
    set_cfg(project, models={'implementer': 'inherit'})
    ar.run(project, max_iterations=1, claude=fake_agent(calls=calls))
    assert '--model' not in calls[0]


def test_dry_run_shows_the_model(project, capsys):
    set_cfg(project, models={'implementer': 'haiku'})
    assert ar.run(project, dry_run=True) == 0
    assert '--model haiku' in capsys.readouterr().out


@pytest.mark.parametrize('role', ['implementer', 'work_reviewer'])
def test_unknown_model_alias_is_refused_by_preflight(project, role):
    calls = []
    set_cfg(project, models={role: 'sonet'})
    with pytest.raises(ar.RunnerError, match=f'models\\.{role}'):
        ar.run(project, max_iterations=1, claude=fake_agent(calls=calls))
    assert calls == [] and not worktree(project).exists()


def picking_agent(calls=None, finish=None):
    """Works the item the prompt names (`Do ONLY item X`): a file, its box ticked, one commit. `finish(item)` False = commit but leave it open."""
    def claude(cmd, cwd):
        cwd = Path(cwd)
        item = cmd[2].split('Do ONLY item ', 1)[1].split()[0]
        if calls is not None:
            calls.append(item)
        q = cwd / '.auto' / 'queue.md'
        if finish is None or finish(item):
            q.write_text(q.read_text(encoding='utf-8').replace(f'- [ ] **{item} ', f'- [x] **{item} ', 1), encoding='utf-8')
        (cwd / f'{item}.txt').write_text('made', encoding='utf-8')
        sh(cwd, 'add', f'{item}.txt')
        sh(cwd, '-c', 'user.email=t@example.com', '-c', 'user.name=T', 'commit', '-q', '-m', item)
        return 0, 'done'
    return claude


def write_queue(p, text):
    (p / '.auto' / 'queue.md').write_text(text, encoding='utf-8')


def project_statuses(p):
    return {i: (s, d) for i, s, d in ar.queue.statuses((p / '.auto' / 'queue.md').read_text(encoding='utf-8'))}


def test_blocked_first_item_is_skipped_and_the_next_ready_one_runs(project, capsys):
    # Before R7 the runner picked A1 again here, the agent made no progress on it twice and the run stopped with exit 1.
    write_queue(project, ar.queue.set_status(QUEUE, 'A1', 'blocked', 'which colour should it be?'))
    calls = []
    assert ar.run(project, max_iterations=4, claude=picking_agent(calls)) == 0
    assert calls == ['A2']
    st = project_statuses(project)
    assert st['A1'] == ('blocked', 'which colour should it be?') and st['A2'][0] == 'done'
    assert [(r['item'], r['result']) for r in runs(project)] == [('A2', 'done')]
    assert 'no ready item' in capsys.readouterr().out
    assert events(project)[-1]['detail'] == {'exit': 0, 'reason': 'no_ready_items'}


def test_run_with_only_blocked_items_ends_cleanly(project, capsys):
    text = ar.queue.set_status(ar.queue.set_status(QUEUE, 'A1', 'blocked', 'q1'), 'A2', 'blocked', 'q2')
    write_queue(project, text)
    calls = []
    assert ar.run(project, max_iterations=4, claude=picking_agent(calls)) == 0
    assert calls == [] and runs(project) == []
    out = capsys.readouterr().out
    assert 'no ready item: 2 open item(s)' in out and 'A1 (blocked)' in out and 'A2 (blocked)' in out
    assert not (project / '.auto' / 'lock').exists()


def test_item_blocked_during_the_run_is_not_rerun(project):
    calls = []
    assert ar.run(project, max_iterations=4, claude=picking_agent(calls, finish=lambda i: i != 'A1')) == 0
    assert calls == ['A1', 'A2']
    assert [r['result'] for r in runs(project)] == ['unfinished', 'done']
    assert project_statuses(project)['A1'][0] == 'blocked'


def test_unmet_depends_on_is_skipped_and_runs_after_its_dependency(project):
    write_queue(project, QUEUE.replace('  *Acceptance:* the file exists.\n\n- [ ] **A2',
                                       '  *Acceptance:* the file exists.\n  *Depends on:* A2\n\n- [ ] **A2', 1))
    calls = []
    assert ar.run(project, max_iterations=4, claude=picking_agent(calls)) == 0
    assert calls == ['A2', 'A1']
    assert [(r['item'], r['result']) for r in runs(project)] == [('A2', 'done'), ('A1', 'done')]


def test_dependency_on_a_blocked_item_waits_and_is_reported(project, capsys):
    text = QUEUE.replace('  *Acceptance:* the file exists.\n\n## Needs', '  *Acceptance:* the file exists.\n  *Depends on:* A1\n\n## Needs', 1)
    write_queue(project, ar.queue.set_status(text, 'A1', 'blocked', 'q'))
    calls = []
    assert ar.run(project, max_iterations=4, claude=picking_agent(calls)) == 0
    assert calls == []
    assert 'A2 (waits for A1)' in capsys.readouterr().out


BLOCKER = {'question': 'Which colour should it be?', 'why_needed': 'The file content depends on it.',
           'prevents': 'Writing the file.', 'impact_if_delayed': 'A1 waits.', 'impact_if_wrong': 'Repainting later.',
           'owner_role': 'product owner', 'class': 'B2',
           'options': [{'label': 'Orange', 'tradeoffs': 'Matches the brand.'}], 'recommendation': 'Orange'}


def blocking_agent(blocked_item, blocker, calls=None):
    """Like picking_agent, but on `blocked_item` it only writes `blocker` (a dict, or raw text) to .auto/blockers/new/ and stops."""
    finish = picking_agent(calls)

    def claude(cmd, cwd):
        item = cmd[2].split('Do ONLY item ', 1)[1].split()[0]
        if item != blocked_item:
            return finish(cmd, cwd)
        if calls is not None:
            calls.append(item)
        new = Path(cwd) / '.auto' / 'blockers' / 'new'
        new.mkdir(parents=True, exist_ok=True)
        (new / 'colour.json').write_text(blocker if isinstance(blocker, str) else json.dumps(blocker), encoding='utf-8')
        return 0, 'blocked'
    return claude


def test_valid_blocker_file_blocks_its_item_and_the_run_moves_on(project):
    calls = []
    assert ar.run(project, max_iterations=4, claude=blocking_agent('A1', BLOCKER, calls)) == 0
    assert calls == ['A1', 'A2']
    blockers, problems = ar.autoblock.Store(project / '.auto').all()
    assert problems == [] and len(blockers) == 1
    b = blockers[0]
    assert (b['state'], b['task'], b['question'], b['project']) == ('OPEN', 'A1', BLOCKER['question'], project.name)
    st = project_statuses(project)
    assert st['A1'] == ('blocked', f'({b["id"]}) Which colour should it be?') and st['A2'][0] == 'done'
    assert f'*Status:* blocked ({b["id"]})' in (project / '.auto' / 'queue.md').read_text(encoding='utf-8')
    assert [(r['item'], r['result']) for r in runs(project)] == [('A1', 'blocked'), ('A2', 'done')]
    ev = [e for e in events(project) if e['type'] == 'blocker']
    assert len(ev) == 1 and ev[0]['item'] == 'A1' and ev[0]['detail'] == {'action': 'raised', 'id': b['id'], 'blocker': ar.autoblock.content(b)}
    assert ar.autoevents.validate(ev[0]) == []
    assert not list((worktree(project) / '.auto' / 'blockers' / 'new').iterdir())       # taken, not left to be raised twice
    view = (project / '.auto' / 'QUESTIONS.md').read_text(encoding='utf-8')
    assert b['id'] in view and 'Which colour should it be?' in view and 'Do not edit' in view
    # a later run keeps A1 blocked and does not raise it again
    assert ar.run(project, max_iterations=2, claude=blocking_agent('A1', BLOCKER), allow_dirty=True) == 0
    assert len(ar.autoblock.Store(project / '.auto').all()[0]) == 1


@pytest.mark.parametrize('content,reason', [
    ('{not json', 'not valid JSON'),
    (json.dumps({**BLOCKER, 'question': ''}), 'question'),
    (json.dumps({**BLOCKER, 'colour': 'red'}), 'unknown field: colour'),
])
def test_invalid_blocker_file_is_rejected_with_reasons_and_the_item_blocked(project, content, reason):
    calls = []
    assert ar.run(project, max_iterations=4, claude=blocking_agent('A1', content, calls)) == 0
    assert calls == ['A1', 'A2']
    st = project_statuses(project)
    assert st['A1'][0] == 'blocked' and 'colour.json was rejected' in st['A1'][1] and reason in st['A1'][1]
    assert ar.autoblock.Store(project / '.auto').all() == ([], [])
    rejected = project / '.auto' / 'blockers' / 'rejected'
    kept = sorted(p.name for p in rejected.iterdir())
    assert len(kept) == 2 and kept[0].endswith('.json') and kept[1].endswith('.reasons.txt')
    assert (rejected / kept[0]).read_text(encoding='utf-8') == content                       # kept exactly as written
    assert reason in (rejected / kept[1]).read_text(encoding='utf-8')
    ev = [e for e in events(project) if e['type'] == 'blocker']
    assert len(ev) == 1 and ev[0]['detail']['action'] == 'rejected' and ev[0]['detail']['file'] == 'colour.json'


def test_blocker_file_waiting_before_the_run_is_rejected_not_pinned_on_an_item(project, capsys):
    stale = project / '.auto' / 'blockers' / 'new'
    stale.mkdir(parents=True)
    (stale / 'old.json').write_text(json.dumps(BLOCKER), encoding='utf-8')
    calls = []
    assert ar.run(project, max_iterations=1, claude=picking_agent(calls)) == 0
    assert calls == ['A1'] and project_statuses(project)['A1'][0] == 'done'
    assert 'old.json was waiting before any iteration' in capsys.readouterr().out
    assert ar.autoblock.Store(project / '.auto').all() == ([], [])
    assert len(list((project / '.auto' / 'blockers' / 'rejected').glob('old-*.json'))) == 1


def test_blocker_on_a_finished_item_is_stored_but_the_item_stays_done(project):
    def claude(cmd, cwd):
        out = picking_agent()(cmd, cwd)
        new = Path(cwd) / '.auto' / 'blockers' / 'new'
        new.mkdir(parents=True, exist_ok=True)
        (new / 'later.json').write_text(json.dumps(BLOCKER), encoding='utf-8')
        return out
    assert ar.run(project, max_iterations=1, claude=claude) == 0
    assert project_statuses(project)['A1'][0] == 'done'
    assert [b['task'] for b in ar.autoblock.Store(project / '.auto').all()[0]] == ['A1']


def test_run_without_blocker_files_gives_the_same_queue_as_before(project):
    assert ar.run(project, max_iterations=2, claude=picking_agent()) == 0
    commits = subprocess.run(['git', '-C', str(worktree(project)), 'log', '--format=%h', '-n', '2', '--abbrev=10'],
                             capture_output=True, text=True).stdout.split()
    expected = QUEUE.replace('- [ ] **A1', '- [x] **A1').replace('- [ ] **A2', '- [x] **A2')
    expected = ar.queue.set_status(ar.queue.set_status(expected, 'A1', 'done', commits[1]), 'A2', 'done', commits[0])
    # the runner writes in text mode, so the bytes carry the platform's line ending
    assert (project / '.auto' / 'queue.md').read_bytes() == expected.replace('\n', os.linesep).encode('utf-8')
    assert not (project / '.auto' / 'blockers').exists() and not (project / '.auto' / 'QUESTIONS.md').exists()
    assert not [e for e in events(project) if e['type'] == 'blocker']


def test_prompt_explains_the_blocker_contract_additively():
    cfg = {'test_command': 'npm test', 'check_only': False, 'queue_file': '.auto/queue.md', 'rules_files': []}
    text = ar.build_prompt(cfg, '.auto/queue.md', 'B7')
    assert '.auto/blockers/new/' in text and 'question, why_needed' in text and '{label, tradeoffs}' in text
    assert "write the question to the project's questions file" in text                    # the old instruction is still there


def test_prompt_names_the_picked_item_and_keeps_the_old_wording_without_one():
    cfg = {'test_command': 'npm test', 'check_only': False, 'queue_file': '.auto/queue.md', 'rules_files': []}
    assert 'Do ONLY item B7 ' in ar.build_prompt(cfg, '.auto/queue.md', 'B7')
    assert "Do ONLY the first item whose box is '[ ]'." in ar.build_prompt(cfg, '.auto/queue.md')


# The prompt exactly as it was before the changelog section existed (Q7); with the changelog off it must stay byte for byte.
OLD_PROMPT_B7 = (
    "You are running unattended. Read RULES.md, then open .auto/queue.md.\n"
    "Do ONLY item B7 (its box is '[ ]'; open items before it are blocked or waiting, leave them alone). Finish it fully: "
    "implementation, tests, the whole suite green (npm test), one git commit on this branch, then tick the box in the same commit. "
    "Do not start a second item. Stay inside the limits in the rules.\n"
    "If you are blocked or the item needs a decision only the product owner can make, write the question to the project's "
    "questions file (or the item's status line), leave the box unticked, and stop.\n"
    "You may instead raise a blocker: write one JSON object per question to .auto/blockers/new/<name>.json with the fields "
    "question, why_needed, prevents, impact_if_delayed, impact_if_wrong, owner_role, class (B1 to B5), options (a list of "
    "{label, tradeoffs}) and recommendation. The runner assigns the id, marks the item blocked and moves on to the next one; "
    "do not tick the box and do not guess the answer.\n"
    "Finish with a short report: what changed, which tests prove it, what is still unverified.")


def test_prompt_is_byte_identical_with_the_changelog_off():
    cfg = {'test_command': 'npm test', 'check_only': False, 'queue_file': '.auto/queue.md', 'rules_files': ['RULES.md']}
    assert ar.build_prompt(cfg, '.auto/queue.md', 'B7') == OLD_PROMPT_B7
    assert ar.build_prompt({**cfg, 'changelog': {'enabled': False}}, '.auto/queue.md', 'B7') == OLD_PROMPT_B7
    assert ar.build_prompt({**cfg, 'changelog': {'file': 'X.md'}}, '.auto/queue.md', 'B7') == OLD_PROMPT_B7
    assert '.auto/changes/' not in ar.build_prompt(cfg, '.auto/queue.md', 'B7', 'PACKET')


def test_prompt_asks_for_the_change_sentence_with_the_changelog_on():
    cfg = {'test_command': 'npm test', 'check_only': False, 'queue_file': '.auto/queue.md', 'rules_files': ['RULES.md'],
           'changelog': {'enabled': True}}
    text = ar.build_prompt(cfg, '.auto/queue.md', 'B7')
    assert '.auto/changes/B7.txt' in text and 'doc map' in text and 'one sentence' in text
    assert text.replace(ar.changelog_paragraph('B7') + '\n', '') == OLD_PROMPT_B7       # the paragraph is purely additive
    assert '.auto/changes/<ITEM>.txt' in ar.build_prompt(cfg, '.auto/queue.md')


def test_enqueue_requires_a_kind_only_with_the_changelog_on(project):
    req = project / '.auto' / 'pending' / 'k' / 'items.md'
    req.parent.mkdir(parents=True)
    req.write_text('- [ ] **Z1 Kindless.** Build a small thing in the app, in enough words to pass the length check here.\n'
                   '  *Acceptance:* a test.\n', encoding='utf-8')
    set_cfg(project, changelog={'enabled': True})
    assert ar.run(project, enqueue='k', dry_run=True, claude=fake_agent()) == 1
    req.write_text(req.read_text(encoding='utf-8') + '  *Kind:* feature\n', encoding='utf-8')
    assert ar.run(project, enqueue='k', dry_run=True, claude=fake_agent()) == 0


def test_changelog_on_adds_one_runner_commit_after_the_agents(project):
    (project / '.auto' / 'queue.md').write_text(QUEUE.replace('  *Acceptance:* the file exists.',
                                                              '  *Acceptance:* the file exists.\n  *Kind:* fix', 1), encoding='utf-8')
    set_cfg(project, changelog={'enabled': True, 'file': 'docs/CHANGES.md'}, commit_trailer='Co-Authored-By: Runner')
    inner = fake_agent()

    def claude(cmd, cwd):
        out = inner(cmd, cwd)
        (Path(cwd) / '.auto' / 'changes').mkdir(parents=True, exist_ok=True)
        (Path(cwd) / '.auto' / 'changes' / 'A1.txt').write_text('Saving a record no longer loses the last edit.', encoding='utf-8')
        return out
    assert ar.run(project, max_iterations=1, claude=claude) == 0
    wt = worktree(project)
    msgs = subprocess.run(['git', '-C', str(wt), 'log', '--format=%B%x00', '-n', '2'], capture_output=True, text=True).stdout
    assert msgs.split('\x00')[0].strip() == 'Changelog: A1 First thing\n\nCo-Authored-By: Runner'
    assert '### Fixed' in (wt / 'docs' / 'CHANGES.md').read_text(encoding='utf-8')
    assert runs(project)[0]['result'] == 'done'


def test_browser_line_without_acceptance_section_runs_no_stage(project):
    q = project / '.auto' / 'queue.md'
    q.write_text(QUEUE.replace('  *Acceptance:* the file exists.', '  *Acceptance:* the file exists.\n  *Browser:* roles: admin', 1),
                 encoding='utf-8')
    called = []
    code = ar.run(project, max_iterations=1, claude=fake_agent(),
                  browser_runner=lambda *a: called.append(a) or (0, ''))
    assert code == 0 and called == []
    assert runs(project)[0]['result'] == 'done' and 'browser' not in runs(project)[0]
    events = [json.loads(x) for x in (project / '.auto' / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert not [e for e in events if e['type'] == 'browser']


# --- Q11: the docs gate (autodocs.gate) after a green item, before the changelog ---

DOC_MAP = {'entries': [{'name': 'runner', 'paths': ['bin/*.py'], 'docs': ['docs/runner.md'], 'stamp': True}]}


def with_doc_map(p, doc_map=DOC_MAP):
    """Commit a doc map, the watched code and its stamped doc on the base branch."""
    (p / 'docs').mkdir(exist_ok=True)
    (p / 'bin').mkdir(exist_ok=True)
    (p / 'bin' / 'run.py').write_text('def run():\n    return 1\n', encoding='utf-8')
    (p / 'docs' / 'runner.md').write_text('# Runner\n\nrun() returns 1.\n', encoding='utf-8')
    (p / 'docs' / 'doc-map.json').write_text(json.dumps(doc_map), encoding='utf-8')
    ar.autodeliver.autostale.revalidate(p, 'docs/runner.md', ['bin/run.py'])
    sh(p, 'add', 'bin', 'docs')
    sh(p, 'commit', '-q', '-m', 'doc map')


def code_agent(revalidate, sentence=None):
    """Ticks the first box and changes the watched code and its doc's text in one commit; revalidates the doc when asked."""
    def claude(cmd, cwd):
        cwd = Path(cwd)
        q = cwd / '.auto' / 'queue.md'
        text = q.read_text(encoding='utf-8')
        item = text.split('- [ ] **', 1)[1].split()[0]
        q.write_text(text.replace('- [ ]', '- [x]', 1), encoding='utf-8')
        (cwd / 'bin' / 'run.py').write_text(f'def run():\n    return {item!r}\n', encoding='utf-8')
        doc = cwd / 'docs' / 'runner.md'
        doc.write_text(doc.read_text(encoding='utf-8') + f'{item} changed what run() returns.\n', encoding='utf-8')
        if revalidate:
            ar.autodeliver.autostale.revalidate(cwd, 'docs/runner.md')
        if sentence:
            (cwd / '.auto' / 'changes').mkdir(parents=True, exist_ok=True)
            (cwd / '.auto' / 'changes' / f'{item}.txt').write_text(sentence, encoding='utf-8')
        sh(cwd, 'add', 'bin', 'docs')
        sh(cwd, '-c', 'user.email=t@example.com', '-c', 'user.name=T', 'commit', '-q', '-m', item)
        return 0, 'done'
    return claude


def test_stale_doc_stops_the_item_with_reason_docs(project):
    with_doc_map(project)
    assert ar.run(project, max_iterations=2, claude=code_agent(revalidate=False)) == 1
    r = runs(project)
    assert len(r) == 1 and r[0]['result'] == 'docs'
    state, detail = project_statuses(project)['A1']
    assert state == 'blocked' and detail.startswith('docs: ') and 'docs/runner.md is stale' in detail
    ev = events(project)
    gates = [e for e in ev if e['type'] == 'docs']
    assert len(gates) == 1 and gates[0]['detail']['result'] == 'fail' and gates[0]['item'] == 'A1'
    assert 'docs/runner.md' in gates[0]['detail']['stale'][0]
    assert ev[-1]['type'] == 'run_end' and ev[-1]['detail']['reason'] == 'docs'


def test_revalidated_doc_completes_and_the_changelog_entry_follows(project):
    (project / '.auto' / 'queue.md').write_text(QUEUE.replace('  *Acceptance:* the file exists.',
                                                              '  *Acceptance:* the file exists.\n  *Kind:* fix', 1), encoding='utf-8')
    set_cfg(project, changelog={'enabled': True})
    with_doc_map(project)
    agent = code_agent(revalidate=True, sentence='The runner now returns two instead of one.')
    assert ar.run(project, max_iterations=1, claude=agent) == 0
    assert runs(project)[0]['result'] == 'done' and runs(project)[0]['changelog']
    ev = events(project)
    kinds = [e['type'] for e in ev if e['type'] in ('docs', 'changelog')]
    assert kinds == ['docs', 'changelog']                                   # the gate runs before the entry
    assert next(e for e in ev if e['type'] == 'docs')['detail'] == {'result': 'pass', 'map': 'docs/doc-map.json',
                                                                      'failures': [], 'stale': []}
    assert '### Fixed' in (worktree(project) / 'CHANGELOG.md').read_text(encoding='utf-8')


def test_unstamped_entry_only_gets_the_q10_rules_in_the_loop(project):
    with_doc_map(project, {'entries': [{'name': 'runner', 'paths': ['bin/*.py'], 'docs': ['docs/runner.md']}]})
    assert ar.run(project, max_iterations=1, claude=code_agent(revalidate=False)) == 0
    gate = next(e for e in events(project) if e['type'] == 'docs')
    assert gate['detail']['result'] == 'pass' and gate['detail']['stale'] == []


def test_no_map_means_no_gate(project):
    assert ar.run(project, max_iterations=2, claude=fake_agent()) == 0
    assert [x['result'] for x in runs(project)] == ['done', 'done']
    assert not [e for e in events(project) if e['type'] == 'docs']


def test_docs_check_false_switches_the_gate_off(project):
    set_cfg(project, docs={'check': False})
    with_doc_map(project)
    assert ar.run(project, max_iterations=1, claude=code_agent(revalidate=False)) == 0
    assert runs(project)[0]['result'] == 'done'
    assert not [e for e in events(project) if e['type'] == 'docs']


def test_one_docs_event_per_gate(project):
    with_doc_map(project)
    assert ar.run(project, max_iterations=2, claude=code_agent(revalidate=True)) == 0
    gates = [e for e in events(project) if e['type'] == 'docs']
    assert [(g['item'], g['detail']['result']) for g in gates] == [('A1', 'pass'), ('A2', 'pass')]
