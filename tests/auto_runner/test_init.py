"""auto-runner plugin: init (preview/apply/remove) and the runner's safety preflight, on scratch git repos."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner' / 'bin'


def _mod(name, file):
    spec = importlib.util.spec_from_file_location(name, BIN / file)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


ai = _mod('autorunner_init_t', 'auto_init.py')
ar = _mod('autorunner_run_t2', 'auto_run.py')
safe = ar.autosafe

PY = f'"{sys.executable}"'
GREEN = f'{PY} -c "import sys; sys.exit(0)"'


def sh(cwd, *args):
    subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True)


def make_repo(tmp_path, branch='main', commit=True, name='proj'):
    p = tmp_path / name
    p.mkdir()
    sh(p, 'init', '-q', '-b', branch)
    sh(p, 'config', 'user.email', 't@example.com')
    sh(p, 'config', 'user.name', 'T')
    (p / 'README.md').write_text('x', encoding='utf-8')
    if commit:
        sh(p, 'add', 'README.md')
        sh(p, 'commit', '-q', '-m', 'init')
    return p


def settings_of(p):
    return json.loads((p / '.claude' / 'settings.json').read_text(encoding='utf-8'))


def ready(p, **cfg):
    """A project that passes the preflight."""
    (p / '.claude').mkdir(exist_ok=True)
    (p / '.claude' / 'settings.json').write_text(json.dumps({'permissions': {'deny': safe.required_rules('main')}}), encoding='utf-8')
    c = {'test_command': GREEN}
    c.update(cfg)
    (p / '.claude' / 'auto.json').write_text(json.dumps(c), encoding='utf-8')
    (p / '.auto').mkdir(exist_ok=True)
    (p / '.auto' / 'queue.md').write_text('# Queue\n', encoding='utf-8')
    sh(p, 'add', '.claude')
    sh(p, 'commit', '-q', '-m', 'setup')


# ---------- init ----------

def test_preview_writes_nothing(tmp_path, capsys):
    p = make_repo(tmp_path)
    assert ai.main(['--project', str(p)]) == 0
    assert 'Preview only' in capsys.readouterr().out
    assert not (p / '.claude').exists() and not (p / '.auto').exists() and not (p / '.gitignore').exists()


def test_existing_settings_survive_and_duplicates_are_skipped(tmp_path):
    p = make_repo(tmp_path)
    (p / '.claude').mkdir()
    original = {'model': 'x', 'permissions': {'allow': ['Bash(ls:*)'], 'deny': ['Bash(git push:*)', 'Bash(rm:*)']}, 'env': {'A': '1'}}
    (p / '.claude' / 'settings.json').write_text(json.dumps(original), encoding='utf-8')
    assert ai.main(['--project', str(p), '--apply']) == 0
    s = settings_of(p)
    assert s['model'] == 'x' and s['env'] == {'A': '1'} and s['permissions']['allow'] == ['Bash(ls:*)']
    assert s['permissions']['deny'][:2] == ['Bash(git push:*)', 'Bash(rm:*)']
    assert s['permissions']['deny'].count('Bash(git push:*)') == 1
    assert not safe.missing_rules(s, 'main')


def test_second_apply_changes_nothing(tmp_path, capsys):
    p = make_repo(tmp_path)
    ai.main(['--project', str(p), '--apply'])
    snap = {f.relative_to(p).as_posix(): f.read_bytes() for f in p.rglob('*') if f.is_file() and '.git' not in f.relative_to(p).parts}
    capsys.readouterr()
    assert ai.main(['--project', str(p), '--apply']) == 0
    assert 'Nothing to do' in capsys.readouterr().out
    after = {f.relative_to(p).as_posix(): f.read_bytes() for f in p.rglob('*') if f.is_file() and '.git' not in f.relative_to(p).parts}
    assert snap == after


def test_apply_creates_config_queue_gitignore_and_detects_tests(tmp_path):
    p = make_repo(tmp_path)
    (p / 'package.json').write_text(json.dumps({'scripts': {'test': 'jest'}}), encoding='utf-8')
    (p / '.gitignore').write_text('node_modules', encoding='utf-8')
    ai.main(['--project', str(p), '--apply'])
    assert json.loads((p / '.claude' / 'auto.json').read_text(encoding='utf-8'))['test_command'] == 'npm test'
    assert (p / '.auto' / 'queue.md').exists()
    assert (p / '.gitignore').read_text(encoding='utf-8').splitlines() == ['node_modules', '.auto/*', '!.auto/queue.md']


@pytest.mark.parametrize('files,expected', [
    ({'tests/test_a.py': ''}, 'python -m pytest -q'),
    ({'app.sln': ''}, 'dotnet test'),
    ({'go.mod': 'module x'}, 'go test ./...'),
    ({'Cargo.toml': '[package]'}, 'cargo test'),
    ({}, ''),
])
def test_test_command_detection(tmp_path, files, expected):
    p = make_repo(tmp_path)
    for rel, text in files.items():
        (p / rel).parent.mkdir(parents=True, exist_ok=True)
        (p / rel).write_text(text, encoding='utf-8')
    assert ai.detect_test_command(p)[0] == expected


def test_no_tests_found_proposes_check_only(tmp_path):
    p = make_repo(tmp_path)
    ai.main(['--project', str(p), '--apply'])
    assert json.loads((p / '.claude' / 'auto.json').read_text(encoding='utf-8')) == {'check_only': True}


def test_existing_auto_command_scripts_and_config_are_reported_not_touched(tmp_path, capsys):
    p = make_repo(tmp_path)
    (p / '.claude' / 'commands').mkdir(parents=True)
    (p / '.claude' / 'commands' / 'auto.md').write_text('mine', encoding='utf-8')
    (p / 'scripts').mkdir()
    (p / 'scripts' / 'auto_run.ps1').write_text('mine', encoding='utf-8')
    (p / '.claude' / 'auto.json').write_text('{"test_command": "make test"}', encoding='utf-8')
    ai.main(['--project', str(p), '--apply'])
    out = capsys.readouterr().out
    assert 'commands/auto.md' in out and 'auto_run.ps1' in out
    assert (p / '.claude' / 'commands' / 'auto.md').read_text(encoding='utf-8') == 'mine'
    assert (p / '.claude' / 'auto.json').read_text(encoding='utf-8') == '{"test_command": "make test"}'


def test_rules_file_offered_only_without_claude_md(tmp_path, capsys):
    p = make_repo(tmp_path)
    ai.main(['--project', str(p)])
    assert 'AUTO_RULES.md' in capsys.readouterr().out and not (p / 'AUTO_RULES.md').exists()
    ai.main(['--project', str(p), '--apply', '--with-rules'])
    assert (p / 'AUTO_RULES.md').exists()
    assert json.loads((p / '.claude' / 'auto.json').read_text(encoding='utf-8'))['rules_files'] == ['AUTO_RULES.md']
    q = make_repo(tmp_path, name='other')
    (q / 'CLAUDE.md').write_text('rules', encoding='utf-8')
    ai.main(['--project', str(q), '--apply', '--with-rules'])
    assert not (q / 'AUTO_RULES.md').exists()
    assert json.loads((q / '.claude' / 'auto.json').read_text(encoding='utf-8'))['rules_files'] == ['CLAUDE.md']


def test_broken_settings_file_is_not_overwritten(tmp_path, capsys):
    p = make_repo(tmp_path)
    (p / '.claude').mkdir()
    (p / '.claude' / 'settings.json').write_text('{not json', encoding='utf-8')
    assert ai.main(['--project', str(p), '--apply']) == 1
    assert (p / '.claude' / 'settings.json').read_text(encoding='utf-8') == '{not json'


def test_master_branch_gets_a_matching_deny_rule(tmp_path):
    p = make_repo(tmp_path, branch='master')
    ai.main(['--project', str(p), '--apply'])
    deny = settings_of(p)['permissions']['deny']
    assert 'Bash(git checkout master:*)' in deny and 'Bash(git switch master:*)' in deny
    assert 'Bash(git checkout main:*)' not in deny


def test_unknown_base_branch_is_reported_and_checkout_rules_skipped(tmp_path, capsys):
    p = make_repo(tmp_path, branch='feature-x')
    ai.main(['--project', str(p), '--apply'])
    assert 'base_branch' in capsys.readouterr().out
    assert not any('checkout' in r for r in settings_of(p)['permissions']['deny'])


def test_remove_removes_only_what_init_added(tmp_path):
    p = make_repo(tmp_path)
    (p / '.claude').mkdir()
    (p / '.claude' / 'settings.json').write_text(json.dumps({'permissions': {'deny': ['Bash(git push:*)', 'Bash(rm:*)']}}), encoding='utf-8')
    (p / '.gitignore').write_text('build/\n', encoding='utf-8')
    ai.main(['--project', str(p), '--apply'])
    (p / '.auto' / 'queue.md').write_text('# my edited queue\n', encoding='utf-8')     # a person's edit must survive
    ai.main(['--project', str(p), '--remove'])                                          # preview: nothing changes
    assert (p / '.claude' / 'auto.json').exists()
    ai.main(['--project', str(p), '--remove', '--apply'])
    deny = settings_of(p)['permissions']['deny']
    assert deny == ['Bash(git push:*)', 'Bash(rm:*)']
    assert (p / '.gitignore').read_text(encoding='utf-8') == 'build/\n'
    assert not (p / '.claude' / 'auto.json').exists()
    assert (p / '.auto' / 'queue.md').read_text(encoding='utf-8') == '# my edited queue\n'
    assert not (p / '.auto' / 'init-manifest.json').exists()


def test_remove_deletes_settings_file_init_created(tmp_path):
    p = make_repo(tmp_path)
    ai.main(['--project', str(p), '--apply'])
    ai.main(['--project', str(p), '--remove', '--apply'])
    assert not (p / '.claude' / 'settings.json').exists() and not (p / '.claude' / 'auto.json').exists()
    assert not (p / '.auto' / 'queue.md').exists() and not (p / '.gitignore').exists() or (p / '.gitignore').read_text(encoding='utf-8') == ''


# ---------- preflight ----------

def test_runner_refuses_without_deny_rules(tmp_path):
    p = make_repo(tmp_path)
    ready(p)
    (p / '.claude' / 'settings.json').write_text(json.dumps({'permissions': {'deny': ['Bash(git push:*)']}}), encoding='utf-8')
    sh(p, 'commit', '-qam', 'weaker')
    with pytest.raises(ar.RunnerError) as e:
        ar.run(p, max_iterations=1, claude=lambda *a: (0, ''))
    msg = str(e.value)
    for need in ('git remote', 'git reset --hard', 'git clean', 'git checkout main', 'reading .env'):
        assert need in msg
    assert 'git push,' not in msg and not (tmp_path / 'proj-auto').exists()


def test_runner_refuses_without_settings_file_even_for_dry_run(tmp_path):
    p = make_repo(tmp_path)
    with pytest.raises(ar.RunnerError, match='Preflight refused'):
        ar.run(p, dry_run=True)


def test_runner_refuses_without_test_command_unless_check_only(tmp_path, capsys):
    p = make_repo(tmp_path)
    ready(p, test_command='')
    with pytest.raises(ar.RunnerError, match='test_command'):
        ar.run(p, dry_run=True)
    (p / '.claude' / 'auto.json').write_text(json.dumps({'check_only': True}), encoding='utf-8')
    assert ar.run(p, dry_run=True, allow_dirty=True) == 0
    assert 'unverified: no tests' in capsys.readouterr().out


def test_runner_refuses_when_there_is_no_commit(tmp_path):
    p = make_repo(tmp_path, commit=False)
    ready_files = p / '.claude'
    ready_files.mkdir()
    (ready_files / 'settings.json').write_text(json.dumps({'permissions': {'deny': safe.required_rules('main')}}), encoding='utf-8')
    (ready_files / 'auto.json').write_text(json.dumps({'test_command': GREEN}), encoding='utf-8')
    with pytest.raises(ar.RunnerError, match='no commit'):
        ar.run(p, dry_run=True)


def test_preflight_passes_on_a_ready_project_and_accepts_powershell_rules(tmp_path):
    p = make_repo(tmp_path)
    ready(p)
    cfg = ar.autocfg.load(p)
    assert safe.preflight(p, cfg, 'main') == []
    rules = [r.replace('Bash(', 'PowerShell(', 1) if r.startswith('Bash(') else r for r in safe.required_rules('main')]
    (p / '.claude' / 'settings.json').write_text(json.dumps({'permissions': {'deny': rules}}), encoding='utf-8')
    assert safe.preflight(p, cfg, 'main') == []


def test_master_repo_needs_a_master_rule(tmp_path):
    p = make_repo(tmp_path, branch='master')
    ready(p)                                           # writes rules for main only
    cfg = ar.autocfg.load(p)
    assert any('git checkout master' in x for x in safe.preflight(p, cfg, 'master'))
