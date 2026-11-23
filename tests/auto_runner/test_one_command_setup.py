"""auto-runner plugin: one-command setup (decision record 0007, step 5). `autocentral.py projects` lists the signed-in
person's projects (real central app through its test client), `sync` brings a checkout up to date without touching
local work, and a folder that is not a checkout gets the way to clone one."""
import subprocess

import pytest

from tests.auto_runner.test_init_central import AT, BASE, SLUG, ac, central  # noqa: F401
from tests.auto_runner.test_init import make_repo, sh


def git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def personal_env(central, tmp_path):
    import datetime
    from central import accounts, personal, projects
    dev = accounts.create(central.conn, 'dev@example.com', 'violet-anchor-58-tide')
    projects.add_member(central.conn, central.project['id'], 'dev@example.com', 'developer', AT)
    token = personal.create_token(central.conn, dev['id'], 'laptop', datetime.datetime.now(datetime.timezone.utc))
    return {'AUTO_RUNNER_TOKEN': token, 'AUTO_RUNNER_CREDENTIALS': str(tmp_path / 'none')}


def test_my_projects(central, personal_env):
    account, found = ac.my_projects(BASE, personal_env, central.send)
    assert account == 'dev@example.com' and [p['slug'] for p in found] == [SLUG] and found[0]['can_run']


def test_my_projects_needs_a_signed_in_computer(central, tmp_path):
    with pytest.raises(ac.LoginError, match='not signed in'):
        ac.my_projects(BASE, {'AUTO_RUNNER_CREDENTIALS': str(tmp_path / 'none')}, central.send)


def test_a_folder_that_is_not_a_checkout_is_told_how_to_clone(tmp_path):
    lines, ok = ac.status(tmp_path, env={})
    assert not ok and 'not a git checkout' in lines[0] and 'projects --central-url' in '\n'.join(lines)


@pytest.fixture
def clone(tmp_path):
    """origin (bare) with main, a clone of it, and a second clone that pushes new commits."""
    src = make_repo(tmp_path, name='src')
    origin = tmp_path / 'origin.git'
    subprocess.run(['git', 'clone', '-q', '--bare', str(src), str(origin)], check=True)
    mine = tmp_path / 'mine'
    subprocess.run(['git', 'clone', '-q', str(origin), str(mine)], check=True)
    for c in (mine,):
        sh(c, 'config', 'user.email', 't@example.com')
        sh(c, 'config', 'user.name', 'T')
    other = tmp_path / 'other'
    subprocess.run(['git', 'clone', '-q', str(origin), str(other)], check=True)
    sh(other, 'config', 'user.email', 't@example.com')
    sh(other, 'config', 'user.name', 'T')
    (other / 'new.txt').write_text('new', encoding='utf-8')
    sh(other, 'add', 'new.txt')
    sh(other, 'commit', '-q', '-m', 'new')
    sh(other, 'push', '-q', 'origin', 'main')
    return mine


def test_sync_fast_forwards_a_clean_base(clone):
    assert ac.sync(clone) == ['Sync: main moved forward 1 commit(s) to origin/main.']
    assert (clone / 'new.txt').exists()
    assert ac.sync(clone) == ['Sync: main is up to date with origin/main.']


def test_sync_never_touches_local_work(clone):
    (clone / 'README.md').write_text('my edit', encoding='utf-8')
    [line] = ac.sync(clone)
    assert 'left as it is because there are uncommitted changes' in line and not (clone / 'new.txt').exists()
    sh(clone, 'checkout', '-q', 'README.md')
    sh(clone, 'switch', '-q', '-c', 'alok/alok-claude-cli/feat/x')
    [line] = ac.sync(clone)
    assert 'because you are on alok/alok-claude-cli/feat/x' in line
    sh(clone, 'switch', '-q', 'main')
    (clone / 'mine.txt').write_text('m', encoding='utf-8')
    sh(clone, 'add', 'mine.txt')
    sh(clone, 'commit', '-q', '-m', 'mine')
    [line] = ac.sync(clone)
    assert 'main has 1 commit(s) of its own' in line and git(clone, 'rev-list', '--count', 'main..origin/main') == '1'


def test_sync_reports_a_fetch_failure(tmp_path):
    p = make_repo(tmp_path)
    sh(p, 'remote', 'add', 'origin', str(tmp_path / 'missing.git'))
    [line] = ac.sync(p)
    assert line.startswith('Sync: could not fetch from origin') and 'Nothing changed' in line
