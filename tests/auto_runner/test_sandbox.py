"""auto-runner plugin: each item's agent runs in Claude Code's sandbox where it works (macOS, Linux and WSL2 with bubblewrap
and socat), with no shell allowlist; elsewhere it keeps the allowlist, or refuses to start when `sandbox.required` is set.
No real sandbox is started: availability is faked and the agent is a fake."""
import json
from pathlib import Path

import pytest

from tests.auto_runner.test_runner import ar, picking_agent, project, set_cfg  # noqa: F401  (project is a fixture)

safe = ar.autosafe
REAL_AVAILABLE = safe.sandbox_available        # taken at import: conftest fakes it during each test


# ---------- where the sandbox works ----------

@pytest.mark.parametrize('platform, tools, expected', [
    ('darwin', set(), (True, '')),
    ('linux', {'bwrap', 'socat'}, (True, '')),
    ('linux', {'socat'}, (False, 'missing bwrap')),
    ('linux', set(), (False, 'missing bwrap and socat')),
    ('win32', {'bwrap', 'socat'}, (False, 'not supported on win32')),
])
def test_sandbox_availability(platform, tools, expected):
    assert REAL_AVAILABLE(platform, lambda name: f'/usr/bin/{name}' if name in tools else None) == expected


def test_only_a_required_sandbox_that_cannot_run_refuses():
    missing = lambda: (False, 'not supported on win32')               # noqa: E731
    assert safe.sandbox_problems({}, missing) == []                    # default: fall back to the allowlist
    assert safe.sandbox_problems({'sandbox': {'required': True, 'enabled': False}}, missing) == []
    assert safe.sandbox_problems({'sandbox': {'required': True}}, lambda: (True, '')) == []
    [problem] = safe.sandbox_problems({'sandbox': {'required': True}}, missing)
    assert 'not available (not supported on win32)' in problem and 'WSL2' in problem


# ---------- configuration ----------

def test_sandbox_defaults_and_overrides(project):
    s = ar.autocfg.sandbox(ar.autocfg.load(project))
    assert s['enabled'] is True and s['required'] is False and 'github.com' in s['allowed_domains']
    set_cfg(project, sandbox={'allowed_domains': ['git.example.com']})
    assert ar.autocfg.sandbox(ar.autocfg.load(project))['allowed_domains'] == ['git.example.com']


def test_a_wrong_sandbox_value_names_the_key(project):
    (project / '.claude' / 'auto.json').write_text(json.dumps({'sandbox': {'enabled': 'yes'}}), encoding='utf-8')
    with pytest.raises(ar.autocfg.ConfigError, match='sandbox.enabled'):
        ar.autocfg.load(project)


# ---------- a run ----------

def recording(cmds):
    inner = picking_agent()

    def claude(cmd, cwd):
        cmds.append(cmd)
        return inner(cmd, cwd)
    return claude


def test_where_the_sandbox_works_each_agent_runs_in_it(project, monkeypatch, capsys):
    monkeypatch.setattr(safe, 'sandbox_available', lambda: (True, ''))
    cmds = []
    assert ar.run(project, max_iterations=3, claude=recording(cmds)) == 0
    for cmd in cmds:
        assert cmd[cmd.index('--settings') + 1].endswith('sandbox-settings.json')
        assert 'Bash(git push:*)' in cmd                                # the agent still never pushes
    settings = json.loads((project / '.auto' / 'run' / 'sandbox-settings.json').read_text(encoding='utf-8'))
    s = settings['sandbox']
    assert s['enabled'] and s['failIfUnavailable'] and s['autoAllowBashIfSandboxed'] and s['allowUnsandboxedCommands'] is False
    assert 'github.com' in s['network']['allowedDomains']
    assert (project / '.git').resolve().as_posix() in s['filesystem']['allowWrite']   # commits of the worktree land there
    assert 'sandbox: on' in capsys.readouterr().out


def test_where_it_does_not_the_agent_keeps_the_allowlist(project, monkeypatch, capsys):
    monkeypatch.setattr(safe, 'sandbox_available', lambda: (False, 'not supported on win32'))
    cmds = []
    assert ar.run(project, max_iterations=3, claude=recording(cmds)) == 0
    assert cmds and all('--settings' not in cmd for cmd in cmds)
    assert all('Bash(git commit:*)' in cmd for cmd in cmds)
    out = capsys.readouterr().out
    assert 'sandbox: not used (not supported on win32)' in out and 'WSL2' in out


def test_turned_off_it_is_not_used_and_says_so_plainly(project, monkeypatch, capsys):
    monkeypatch.setattr(safe, 'sandbox_available', lambda: (True, ''))
    set_cfg(project, sandbox={'enabled': False})
    cmds = []
    assert ar.run(project, max_iterations=3, claude=recording(cmds)) == 0
    assert all('--settings' not in cmd for cmd in cmds)
    assert 'sandbox: not used (turned off); the agent keeps the shell allowlist\n' in capsys.readouterr().out


def test_required_and_unavailable_refuses_to_start(project, monkeypatch):
    monkeypatch.setattr(safe, 'sandbox_available', lambda: (False, 'missing bwrap'))
    set_cfg(project, sandbox={'required': True})
    with pytest.raises(ar.RunnerError, match='sandbox.required is true'):
        ar.run(project, max_iterations=1, claude=picking_agent())
    assert not (Path(project) / '.auto' / 'run' / 'sandbox-settings.json').exists()
