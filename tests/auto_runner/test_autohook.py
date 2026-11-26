"""auto-runner plugin: the UserPromptSubmit reminder hook (bin/autohook.py prompt) and hooks/hooks.json."""
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / 'auto-runner'
BIN = PLUGIN / 'bin'

_spec = importlib.util.spec_from_file_location('autorunner_hook_t', BIN / 'autohook.py')
ah = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ah)

CENTRAL = {'central': {'enabled': True, 'url': 'https://central.test', 'project': 'acme-web'}}


def project(tmp_path, config=None, raw=None):
    p = tmp_path / 'proj'
    (p / '.claude').mkdir(parents=True)
    if raw is not None:
        (p / '.claude' / 'auto.json').write_text(raw, encoding='utf-8')
    elif config is not None:
        (p / '.claude' / 'auto.json').write_text(json.dumps(config), encoding='utf-8')
    return p


def run(p, prompt='add a CSV export', env=None, stdin=None):
    out = io.StringIO()
    body = json.dumps({'session_id': 's1', 'cwd': str(p), 'prompt': prompt}) if stdin is None else stdin
    code = ah.main(['prompt'], stdin=io.StringIO(body), stdout=out, env={} if env is None else env)
    return code, out.getvalue()


def test_onboarded_project_gets_one_line_reminder(tmp_path):
    code, out = run(project(tmp_path, CENTRAL))
    assert code == 0
    assert 'autotasks.py propose' in out and '/auto-runner:auto' in out
    assert out.count('\n') == 1


def test_claude_project_dir_wins_over_cwd(tmp_path):
    p = project(tmp_path, CENTRAL)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    out = io.StringIO()
    body = json.dumps({'cwd': str(elsewhere), 'prompt': 'fix it'})
    ah.main(['prompt'], stdin=io.StringIO(body), stdout=out, env={'CLAUDE_PROJECT_DIR': str(p)})
    assert 'autotasks.py propose' in out.getvalue()


def test_not_onboarded_prints_nothing(tmp_path):
    assert run(project(tmp_path)) == (0, '')                                   # no auto.json
    assert run(project(tmp_path / 'b', {'base_branch': 'main'})) == (0, '')    # no central section
    off = {'central': {'enabled': False, 'url': 'https://central.test', 'project': 'x'}}
    assert run(project(tmp_path / 'c', off)) == (0, '')


def test_slash_command_prints_nothing(tmp_path):
    p = project(tmp_path, CENTRAL)
    assert run(p, prompt='/auto-runner:auto add search') == (0, '')
    assert run(p, prompt='  /help') == (0, '')


def test_broken_config_prints_nothing_and_exits_0(tmp_path):
    assert run(project(tmp_path, raw='{not json')) == (0, '')
    assert run(project(tmp_path / 'b', {'nonsense_key': 1, **CENTRAL})) == (0, '')
    assert run(project(tmp_path / 'c', raw='[1, 2]')) == (0, '')


def test_unreadable_hook_input_exits_0(tmp_path):
    p = project(tmp_path, CENTRAL)
    assert run(p, stdin='not json') == (0, '')
    assert ah.main(['unknown'], stdin=io.StringIO('{}'), stdout=io.StringIO(), env={}) == 0


def test_runs_as_a_real_process(tmp_path):
    p = project(tmp_path, CENTRAL)
    body = json.dumps({'cwd': str(p), 'prompt': 'add search'})
    r = subprocess.run([sys.executable, str(BIN / 'autohook.py'), 'prompt'], input=body, capture_output=True,
                       text=True, cwd=str(tmp_path), env={'PATH': '', 'SYSTEMROOT': ''})
    assert r.returncode == 0
    assert 'autotasks.py propose' in r.stdout


def test_hooks_json_points_every_command_at_autohook():
    data = json.loads((PLUGIN / 'hooks' / 'hooks.json').read_text(encoding='utf-8'))
    events = data['hooks']
    assert 'UserPromptSubmit' in events
    commands = [h['command'] for groups in events.values() for g in groups for h in g['hooks']]
    assert commands
    for c in commands:
        assert '${CLAUDE_PLUGIN_ROOT}/bin/autohook.py' in c
    prompt_cmds = [h['command'] for g in events['UserPromptSubmit'] for h in g['hooks']]
    assert any(c.rstrip().endswith('autohook.py" prompt') for c in prompt_cmds)


# --- H5: the opt-in guard (PreToolUse) and the /auto-runner:allow-edits escape --------------------------------------

GUARDED = {'enforce_runner': True}
T0 = 1_800_000_000.0


def guarded(tmp_path, config=GUARDED):
    p = project(tmp_path, config)
    (p / '.git').mkdir()                                  # the main checkout: .git is a folder
    return p


def guard(p, path, sid='s1', tool='Edit', now=T0, env=None):
    key = 'notebook_path' if tool == 'NotebookEdit' else 'file_path'
    body = json.dumps({'session_id': sid, 'cwd': str(p), 'hook_event_name': 'PreToolUse', 'tool_name': tool,
                       'tool_input': {key: str(path)}})
    err = io.StringIO()
    code = ah.main(['guard'], stdin=io.StringIO(body), stdout=io.StringIO(), stderr=err,
                   env={'CLAUDE_PROJECT_DIR': str(p)} if env is None else env, now=now)
    return code, err.getvalue()


def type_allow_edits(p, sid='s1', now=T0):
    out = io.StringIO()
    body = json.dumps({'session_id': sid, 'cwd': str(p), 'prompt': '/auto-runner:allow-edits'})
    code = ah.main(['prompt'], stdin=io.StringIO(body), stdout=out, env={'CLAUDE_PROJECT_DIR': str(p)}, now=now)
    return code, out.getvalue()


def test_guard_blocks_main_checkout_paths(tmp_path):
    p = guarded(tmp_path)
    for tool in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
        code, err = guard(p, p / 'src' / 'app.py', tool=tool)
        assert code == 2
        assert '/auto-runner:auto' in err and 'src/app.py' in err and '/auto-runner:allow-edits' in err
    assert guard(p, 'README.md')[0] == 2                  # a relative path is taken from the session's cwd


def test_guard_allows_runner_worktree_auto_dir_and_outside(tmp_path):
    p = guarded(tmp_path)
    wt = tmp_path / 'proj-auto'
    wt.mkdir()
    assert guard(p, wt / 'src' / 'app.py') == (0, '')
    assert guard(p, p / '.auto' / 'queue.md') == (0, '')
    assert guard(p, p / '.auto' / 'blockers' / 'new' / 'q.json') == (0, '')
    assert guard(p, tmp_path / 'other' / 'notes.md') == (0, '')
    assert guard(p, p / '.autox' / 'f')[0] == 2           # only .auto/ itself, not a look-alike


def test_guard_allows_everything_inside_a_linked_worktree(tmp_path):
    wt = project(tmp_path, GUARDED)
    (wt / '.git').write_text('gitdir: /elsewhere/.git/worktrees/proj\n', encoding='utf-8')
    assert guard(wt, wt / 'src' / 'app.py') == (0, '')


def test_guard_is_off_by_default(tmp_path):
    assert guard(guarded(tmp_path, {}), tmp_path / 'proj' / 'src' / 'app.py') == (0, '')
    assert guard(guarded(tmp_path / 'b', {'enforce_runner': False}), tmp_path / 'b' / 'proj' / 'x.py') == (0, '')
    assert guard(guarded(tmp_path / 'c', None), tmp_path / 'c' / 'proj' / 'x.py') == (0, '')    # no auto.json


def test_guard_allows_when_it_cannot_decide(tmp_path):
    p = guarded(tmp_path)
    err = io.StringIO()
    assert ah.main(['guard'], stdin=io.StringIO('not json'), stdout=io.StringIO(), stderr=err, env={}) == 0
    body = json.dumps({'cwd': str(p), 'tool_name': 'Edit', 'tool_input': {}})
    assert ah.main(['guard'], stdin=io.StringIO(body), stdout=io.StringIO(), stderr=err, env={}) == 0
    bad = project(tmp_path / 'b', raw='{oops')
    (bad / '.git').mkdir()
    assert guard(bad, bad / 'x.py') == (0, '')
    assert err.getvalue() == ''


def test_allow_edits_opens_the_escape_for_that_session_only(tmp_path):
    p = guarded(tmp_path)
    code, out = type_allow_edits(p, sid='s1')
    assert code == 0 and '12 hours' in out and 'autotasks.py propose' not in out
    assert (p / '.auto' / 'run' / 'allow-s1').is_file()
    assert guard(p, p / 'src' / 'app.py', sid='s1') == (0, '')
    assert guard(p, p / 'src' / 'app.py', sid='s2')[0] == 2
    (p / '.auto' / 'run' / 'allow-s1').unlink()            # deleting the file ends it
    assert guard(p, p / 'src' / 'app.py', sid='s1')[0] == 2


def test_allow_expires_after_12_hours(tmp_path):
    p = guarded(tmp_path)
    type_allow_edits(p, sid='s1', now=T0)
    assert guard(p, p / 'a.py', now=T0 + 12 * 3600 - 1) == (0, '')
    assert guard(p, p / 'a.py', now=T0 + 12 * 3600)[0] == 2


def test_allow_command_and_unsafe_session_ids(tmp_path):
    p = guarded(tmp_path)
    out = io.StringIO()
    body = json.dumps({'session_id': 'abc-123', 'cwd': str(p)})
    assert ah.main(['allow'], stdin=io.StringIO(body), stdout=out, env={}, now=T0) == 0
    assert (p / '.auto' / 'run' / 'allow-abc-123').is_file() and '12 hours' in out.getvalue()
    for sid in ('../evil', '', None, '..', 'a/b'):
        assert type_allow_edits(p, sid=sid) == (0, '')
    assert sorted(f.name for f in (p / '.auto' / 'run').iterdir()) == ['allow-abc-123']


def test_guard_runs_as_a_real_process(tmp_path):
    p = guarded(tmp_path)
    body = json.dumps({'session_id': 's1', 'cwd': str(p), 'tool_name': 'Write',
                       'tool_input': {'file_path': str(p / 'x.py')}})
    r = subprocess.run([sys.executable, str(BIN / 'autohook.py'), 'guard'], input=body, capture_output=True,
                       text=True, cwd=str(tmp_path), env={'PATH': '', 'SYSTEMROOT': ''})
    assert r.returncode == 2 and '/auto-runner:auto' in r.stderr


def test_hooks_json_registers_the_guard():
    data = json.loads((PLUGIN / 'hooks' / 'hooks.json').read_text(encoding='utf-8'))
    groups = data['hooks']['PreToolUse']
    assert [g['matcher'] for g in groups] == ['Edit|Write|MultiEdit|NotebookEdit']
    cmds = [h['command'] for h in groups[0]['hooks']]
    assert cmds == ['python "${CLAUDE_PLUGIN_ROOT}/bin/autohook.py" guard']
    assert (PLUGIN / 'commands' / 'allow-edits.md').is_file()


def test_manual_edits_is_the_new_name_of_allow_edits():
    assert ah.is_allow_prompt({'prompt': '/auto-runner:manual edits'})
    assert ah.is_allow_prompt({'prompt': '/auto-runner:allow-edits'})
    assert not ah.is_allow_prompt({'prompt': '/auto-runner:manual on'})
    assert not ah.is_allow_prompt({'prompt': '/auto-runner:manual'})
