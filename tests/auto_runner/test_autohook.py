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
