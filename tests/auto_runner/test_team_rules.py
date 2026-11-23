"""auto-runner plugin: the team git rules. A run needs who runs it (AUTO_RUNNER_USER, AUTO_RUNNER_ACCOUNT); branches are
`<user>/<account>-claude-auto-runner/<type>/<brief>`; the base is fetched from the remote first; each request gets its own branch and later requests wait
until it is merged. Remotes are local bare repositories; nothing reaches the network."""
import subprocess

import pytest

from tests.auto_runner.test_friction import commit_on, worktree
from tests.auto_runner.test_publish import bare_origin
from tests.auto_runner.test_runner import ar, fake_agent, picking_agent, project, sh  # noqa: F401  (project is a fixture)

safe = ar.autosafe

ITEM = ('- [ ] **{id} {title}.** Build the {title} in a small file, with enough words to pass any length rule we might have.\n'
        '  *Acceptance:* the file exists.\n  *Slice:* 1 The {title}\n')


def request(p, slug, item_id, kind=None):
    d = p / '.auto' / 'pending' / slug
    d.mkdir(parents=True)
    text = ITEM.format(id=item_id, title=f'thing {item_id}')
    (d / 'items.md').write_text(text + (f'  *Kind:* {kind}\n' if kind else ''), encoding='utf-8')
    return d


# ---------- who runs it ----------

@pytest.mark.parametrize('env,needles', [
    ({}, ['Set AUTO_RUNNER_USER', 'Set AUTO_RUNNER_ACCOUNT']),
    ({'AUTO_RUNNER_USER': 'alok'}, ['Set AUTO_RUNNER_ACCOUNT']),
    ({'AUTO_RUNNER_USER': 'Alok Sharma', 'AUTO_RUNNER_ACCOUNT': 'bhawish'}, ['AUTO_RUNNER_USER ("Alok Sharma") must be']),
    ({'AUTO_RUNNER_USER': 'alok', 'AUTO_RUNNER_ACCOUNT': 'a..b'}, ['AUTO_RUNNER_ACCOUNT ("a..b") must be']),
    ({'AUTO_RUNNER_USER': '-x', 'AUTO_RUNNER_ACCOUNT': 'x.lock'}, ['AUTO_RUNNER_USER ("-x")', 'AUTO_RUNNER_ACCOUNT ("x.lock")']),
])
def test_a_missing_or_bad_identity_is_refused(env, needles):
    problems = safe.runner_name_problems(env)
    assert len(problems) == len(needles) and all(n in p for n, p in zip(needles, problems))
    assert safe.runner_name(env) == ''


def test_a_good_identity_gives_the_branch_root():
    env = {'AUTO_RUNNER_USER': 'alok', 'AUTO_RUNNER_ACCOUNT': 'bhawish'}
    assert safe.runner_name_problems(env) == []
    assert safe.runner_name(env) == 'alok/bhawish-claude-auto-runner'


@pytest.mark.parametrize('var', ['AUTO_RUNNER_USER', 'AUTO_RUNNER_ACCOUNT'])
def test_the_run_refuses_without_either(project, monkeypatch, var):
    monkeypatch.delenv(var)
    with pytest.raises(ar.RunnerError, match=f'Set {var}'):
        ar.run(project, max_iterations=1, claude=fake_agent())
    assert not worktree(project).exists()


def test_init_preview_says_how_to_set_them(project):
    init = ar._load('autorunner_init_t', 'auto_init.py')
    notes = init.make_plan(project, {'local': True}, env={'AUTO_RUNNER_USER': 'alok'})['notes']
    assert any('Set AUTO_RUNNER_ACCOUNT' in n and "SetEnvironmentVariable('AUTO_RUNNER_ACCOUNT'" in n for n in notes)
    assert not any('AUTO_RUNNER_USER' in n for n in notes)
    full = {'AUTO_RUNNER_USER': 'alok', 'AUTO_RUNNER_ACCOUNT': 'bhawish'}
    assert not any('AUTO_RUNNER' in n for n in init.make_plan(project, {'local': True}, env=full)['notes'])


# ---------- branch names ----------

@pytest.mark.parametrize('kinds,expected', [((), 'feat'), (('fix',), 'fix'), (('internal',), 'chore'),
                                            (('fix', 'feature'), 'feat'), (('breaking',), 'feat'),
                                            (('internal', 'fix'), 'fix')])
def test_branch_type_follows_the_kind_lines(kinds, expected):
    text = ''.join(ITEM.format(id=f'A{i}', title='x') + f'  *Kind:* {k}\n' for i, k in enumerate(kinds, 1))
    assert ar.branch_type(text) == expected


def test_brief_and_numbering():
    assert ar.branch_brief('Add Login_Page!!') == 'add-login-page'
    assert ar.branch_brief('x' * 60) == 'x' * 40 and ar.branch_brief('***') == 'work'
    me = 'alok/bhawish-claude-auto-runner'
    used = {f'{me}/feat/login', f'{me}/feat/login-2'}
    assert ar.run_branch(me, 'feat', 'login', used.__contains__) == f'{me}/feat/login-3'
    assert ar.run_branch(me, 'fix', 'login', used.__contains__) == f'{me}/fix/login'
    assert ar.pr_title(f'{me}/fix/login-page', me, 2) == 'fix: login page (alok via bhawish-claude-auto-runner)'
    assert ar.pr_title('someone/else/fix/x', me, 2) == 'auto-runner: 2 commit(s) from someone/else/fix/x'


def test_a_request_names_the_branch(project):
    request(project, 'Fix-Login', 'A3', kind='fix')
    assert ar.run(project, max_iterations=1, claude=picking_agent(), allow_dirty=True) == 0
    assert ar.git(worktree(project), 'branch', '--show-current') == 'tester/acct-claude-auto-runner/fix/fix-login'


# ---------- the base comes from the remote ----------

def test_the_run_starts_from_the_fetched_remote_base(project, tmp_path, capsys):
    remote = bare_origin(project, tmp_path)
    other = tmp_path / 'other'                                     # a teammate pushes to main; this checkout never pulled
    subprocess.run(['git', 'clone', '-q', '-b', 'main', str(remote), str(other)], check=True, capture_output=True)
    commit_on(other, 'teammate.txt')
    sh(other, 'push', '-q', 'origin', 'main')
    assert ar.run(project, max_iterations=1, claude=picking_agent(), pr_runner=lambda argv, cwd: (0, '')) == 0
    assert (worktree(project) / 'teammate.txt').exists() and not (project / 'teammate.txt').exists()
    assert 'fetched main from origin: starting from origin/main' in capsys.readouterr().out


def test_a_failed_fetch_stops_the_run(project, tmp_path):
    sh(project, 'remote', 'add', 'origin', str(tmp_path / 'missing.git'))
    with pytest.raises(ar.RunnerError, match='could not update main from origin'):
        ar.run(project, max_iterations=1, claude=fake_agent())
    assert not worktree(project).exists()


def test_a_remote_without_the_base_falls_back_to_the_local_base(project, tmp_path, capsys):
    subprocess.run(['git', 'init', '-q', '--bare', str(tmp_path / 'empty.git')], check=True, capture_output=True)
    sh(project, 'remote', 'add', 'origin', str(tmp_path / 'empty.git'))
    assert ar.run(project, max_iterations=1, claude=fake_agent(), pr_runner=lambda argv, cwd: (0, '')) == 0
    assert 'origin has no branch main yet: starting from the local main' in capsys.readouterr().out


def test_a_dry_run_only_says_it_would_fetch(project, tmp_path, capsys):
    bare_origin(project, tmp_path)
    assert ar.run(project, dry_run=True, claude=fake_agent()) == 0
    out = capsys.readouterr().out
    assert 'would fetch main from origin and start from origin/main' in out and 'tester/acct-claude-auto-runner/feat/queue-' in out


# ---------- one request per branch ----------

def test_a_new_branch_takes_only_the_oldest_request(project, capsys):
    request(project, 'aa-first', 'A3')
    request(project, 'bb-second', 'A4')
    assert ar.run(project, max_iterations=1, claude=picking_agent(), allow_dirty=True) == 0
    queue_text = (project / '.auto' / 'queue.md').read_text(encoding='utf-8')
    assert '**A3' in queue_text and '**A4' not in queue_text
    assert (project / '.auto' / 'pending' / 'bb-second').exists()
    assert ar.git(worktree(project), 'branch', '--show-current') == 'tester/acct-claude-auto-runner/feat/aa-first'
    assert "request 'bb-second' waits: tester/acct-claude-auto-runner/feat/aa-first is not merged into main yet" in capsys.readouterr().out


def test_an_unmerged_branch_keeps_new_requests_waiting(project, capsys):
    assert ar.run(project, max_iterations=1, claude=picking_agent()) == 0          # A1 done; the branch is not merged
    wt = worktree(project)
    branch = ar.git(wt, 'branch', '--show-current')
    request(project, 'later', 'A3')
    capsys.readouterr()
    assert ar.run(project, max_iterations=1, claude=picking_agent(), allow_dirty=True) == 0
    out = capsys.readouterr().out
    assert ar.git(wt, 'branch', '--show-current') == branch and 'continuing its items' in out
    assert "request 'later' waits" in out and (project / '.auto' / 'pending' / 'later').exists()
    assert '**A3' not in (wt / '.auto' / 'queue.md').read_text(encoding='utf-8')


def test_after_the_merge_the_waiting_request_gets_its_own_branch(project, capsys):
    assert ar.run(project, max_iterations=1, claude=picking_agent()) == 0
    wt = worktree(project)
    first = ar.git(wt, 'branch', '--show-current')
    request(project, 'later', 'A3', kind='fix')
    sh(project, 'merge', '-q', '--ff-only', first)                                 # a person merges the first PR
    assert ar.run(project, max_iterations=1, claude=picking_agent(), allow_dirty=True) == 0
    assert ar.git(wt, 'branch', '--show-current') == 'tester/acct-claude-auto-runner/fix/later'
    assert not (project / '.auto' / 'pending' / 'later').exists()


# ---------- who ran it ----------

def test_run_start_names_who_ran_it_and_run_ids_are_unique(project):
    import json
    import re
    assert ar.run(project, max_iterations=1, claude=picking_agent()) == 0
    assert ar.run(project, max_iterations=1, claude=picking_agent(), allow_dirty=True) == 0
    events = [json.loads(line) for line in (project / '.auto' / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    starts = [e for e in events if e['type'] == 'run_start']
    assert len(starts) == 2 and starts[0]['run'] != starts[1]['run']
    assert all(re.fullmatch(r'\d{8}-\d{4}-[0-9a-f]{6}', e['run']) for e in starts)
    assert starts[0]['detail']['user'] == 'tester' and starts[0]['detail']['account'] == 'acct'
