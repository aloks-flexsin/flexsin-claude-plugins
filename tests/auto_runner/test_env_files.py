"""auto-runner plugin: `env_files` (gitignored config such as `.env`, copied into the run's worktree, values redacted) and
`safety.allow_env_read` (the owner's opt-out from the `.env` read deny rules)."""
import json
import sys
from pathlib import Path

import pytest

from tests.auto_runner.test_init import ai
from tests.auto_runner.test_runner import ar, picking_agent, project, project_statuses, sh  # noqa: F401  (project is a fixture)

safe = ar.autosafe
SECRET = 'mongodb+srv://app:s3cret-Passw0rd@cluster0.example.net/app'
ENV_TEXT = f'# local settings\nexport MONGO_URI="{SECRET}"\nPORT=3000\nAPI_KEY=\'sk-test-1234567890\'\nEMPTY=\n'


def ignored_env(p, text=ENV_TEXT, name='.env'):
    (p / '.gitignore').write_text(f'{name}\n', encoding='utf-8')
    (p / name).write_text(text, encoding='utf-8')
    sh(p, 'add', '.gitignore')
    sh(p, 'commit', '-q', '-m', 'ignore env')


# ---------- the opt-out ----------

def test_allow_env_read_drops_only_the_env_rules():
    assert 'Read(./.env)' in safe.required_rules('main')
    rules = safe.required_rules('main', env_read=True)
    assert not any('.env' in r for r in rules) and 'Bash(git push:*)' in rules


def test_preflight_honours_the_opt_out(project):
    settings = {'permissions': {'deny': safe.required_rules('main', env_read=True)}}
    assert safe.missing_rules(settings, 'main') == ['reading .env files']
    assert safe.missing_rules(settings, 'main', env_read=True) == []
    (project / '.claude' / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
    cfg = {**ar.autocfg.load(project)}
    assert any('.env' in p for p in safe.preflight(project, cfg, 'main'))
    cfg['safety'] = {'allow_env_read': True}
    assert safe.preflight(project, cfg, 'main') == []


def test_allow_env_read_must_be_true_not_truthy():
    assert not safe.env_read_allowed({'safety': {}}) and not safe.env_read_allowed({})
    assert safe.env_read_allowed({'safety': {'allow_env_read': True}})


# ---------- env_files checks ----------

def test_a_gitignored_untracked_file_passes(project):
    ignored_env(project)
    assert safe.env_file_problems(project, {'env_files': ['.env']}) == []


@pytest.mark.parametrize('entry,needle', [('/etc/app.env', 'inside the project'), ('../.env', 'inside the project'),
                                          ('', 'inside the project'), ('missing.env', 'does not exist')])
def test_bad_entries_are_refused(project, entry, needle):
    [problem] = safe.env_file_problems(project, {'env_files': [entry]})
    assert needle in problem


def test_a_file_git_would_commit_is_refused(project):
    (project / '.env').write_text(ENV_TEXT, encoding='utf-8')
    [problem] = safe.env_file_problems(project, {'env_files': ['.env']})
    assert 'must be ignored by git' in problem


def test_a_tracked_file_is_refused_even_when_ignored(project):
    (project / '.env').write_text(ENV_TEXT, encoding='utf-8')
    sh(project, 'add', '.env')
    sh(project, 'commit', '-q', '-m', 'oops')
    (project / '.gitignore').write_text('.env\n', encoding='utf-8')
    [problem] = safe.env_file_problems(project, {'env_files': ['.env']})
    assert 'git rm --cached' in problem


def test_values_to_redact(tmp_path):
    f = tmp_path / '.env'
    f.write_text(ENV_TEXT, encoding='utf-8')
    assert safe.env_file_values([f, tmp_path / 'missing']) == {'.env:MONGO_URI': SECRET, '.env:API_KEY': 'sk-test-1234567890'}


def test_copy_is_a_copy_and_never_overwrites(tmp_path):
    src, wt = tmp_path / 'p', tmp_path / 'wt'
    (src / 'config').mkdir(parents=True)
    (src / 'config' / '.env').write_text('A=1\n', encoding='utf-8')
    ar.copy_env_files(src, wt, ['config/.env'])
    (wt / 'config' / '.env').write_text('A=changed in the worktree\n', encoding='utf-8')
    assert (src / 'config' / '.env').read_text(encoding='utf-8') == 'A=1\n'
    ar.copy_env_files(src, wt, ['config/.env'])
    assert (wt / 'config' / '.env').read_text(encoding='utf-8') == 'A=changed in the worktree\n'


# ---------- a run ----------

def test_a_run_has_the_env_file_and_never_logs_its_values(project):
    ignored_env(project)
    check = f'"{sys.executable}" -c "import pathlib,sys; sys.exit(0 if \'MONGO_URI\' in pathlib.Path(\'.env\').read_text() else 1)"'
    (project / '.claude' / 'auto.json').write_text(json.dumps({'test_command': check, 'env_files': ['.env']}), encoding='utf-8')
    sh(project, 'add', '.claude/auto.json')
    sh(project, 'commit', '-q', '-m', 'cfg')
    work = picking_agent()

    def agent(cmd, cwd):
        code, out = work(cmd, cwd)
        return code, f'{out}\nconnected to {SECRET} with key sk-test-1234567890\n'

    assert ar.run(project, max_iterations=2, claude=agent) == 0
    assert project_statuses(project)['A1'][0] == 'done'               # the suite saw the .env in the worktree
    for f in (project / '.auto').rglob('*'):
        if f.is_file():
            text = f.read_text(encoding='utf-8', errors='replace')
            assert SECRET not in text and 'sk-test-1234567890' not in text, f


def test_a_run_refuses_an_env_file_git_would_commit(project):
    (project / '.env').write_text(ENV_TEXT, encoding='utf-8')
    (project / '.claude' / 'auto.json').write_text(json.dumps({'test_command': 'x', 'env_files': ['.env']}), encoding='utf-8')
    sh(project, 'add', '.claude/auto.json')
    sh(project, 'commit', '-q', '-m', 'cfg')
    with pytest.raises(ar.RunnerError, match='must be ignored by git'):
        ar.run(project, max_iterations=1, claude=picking_agent())


# ---------- init ----------

def test_init_proposes_an_ignored_env_file(tmp_path):
    p = tmp_path / 'proj'
    p.mkdir()
    sh(p, 'init', '-q', '-b', 'main')
    (p / '.gitignore').write_text('.env\n', encoding='utf-8')
    (p / '.env').write_text(ENV_TEXT, encoding='utf-8')
    plan = ai.make_plan(p, {'local': True})
    assert plan['new_cfg']['env_files'] == ['.env'] and 'safety' not in plan['new_cfg']
    assert 'Read(./.env)' in plan['rules']


def test_init_does_not_propose_an_env_file_git_would_commit(tmp_path):
    p = tmp_path / 'proj'
    p.mkdir()
    sh(p, 'init', '-q', '-b', 'main')
    (p / '.env').write_text(ENV_TEXT, encoding='utf-8')
    plan = ai.make_plan(p, {'local': True})
    assert 'env_files' not in plan['new_cfg']
    assert any('not copied' in n for n in plan['notes'])


def test_init_allow_env_read_writes_the_opt_out_and_skips_the_rules(tmp_path):
    p = tmp_path / 'proj'
    p.mkdir()
    sh(p, 'init', '-q', '-b', 'main')
    assert ai.main(['--project', str(p), '--local', '--allow-env-read', '--apply']) == 0
    cfg = json.loads((p / '.claude' / 'auto.json').read_text(encoding='utf-8'))
    deny = json.loads((p / '.claude' / 'settings.json').read_text(encoding='utf-8'))['permissions']['deny']
    assert cfg['safety'] == {'allow_env_read': True} and not any('.env' in r for r in deny)
    assert ai.make_plan(p, {'local': True})['rules'] == []                # preflight and init agree


def test_init_allow_env_read_does_not_edit_an_existing_auto_json(tmp_path):
    p = tmp_path / 'proj'
    p.mkdir()
    sh(p, 'init', '-q', '-b', 'main')
    (p / '.claude').mkdir()
    (p / '.claude' / 'auto.json').write_text('{"test_command": "x"}', encoding='utf-8')
    plan = ai.make_plan(p, {'local': True}, allow_env_read=True)
    assert 'Read(./.env)' in plan['rules'] and any('--allow-env-read only applies' in n for n in plan['notes'])
