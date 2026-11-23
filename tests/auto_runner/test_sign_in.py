"""auto-runner plugin: sign in from Claude (decision record 0008). `autocentral.py login start` shows a code, the person
approves it on the real central app (Flask test client, no socket), `login wait` stores the personal token without
printing it, and status, init and the runner then use it."""
import os
import re
import stat
import sys

import pytest

from tests.auto_runner.test_init_central import AT, BASE, REPO, SLUG, ac, ai, central, repo  # noqa: F401
from tests.auto_runner.test_runner import ar

safe = ai.autosafe
PW = 'violet-anchor-58-tide'
ME = {'AUTO_RUNNER_USER': 'alok', 'AUTO_RUNNER_ACCOUNT': 'bhawish'}


def browser(central, email, role='developer'):
    """A signed-in browser on the central app for a new account with `role` in the project."""
    from central import accounts, projects
    accounts.create(central.conn, email, PW)
    if role:
        projects.add_member(central.conn, central.project['id'], email, role, AT)
    c = central.client.application.test_client()
    c.environ_base['wsgi.url_scheme'] = 'https'
    tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get('/signin').get_data(as_text=True)).group(1)
    assert c.post('/signin', data={'email': email, 'password': PW, 'csrf_token': tok}).status_code == 302
    return c


def approve(c, code, decision='approve'):
    tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get('/device').get_data(as_text=True)).group(1)
    c.post('/device', data={'user_code': code, 'csrf_token': tok})
    return c.post('/device/decide', data={'user_code': code, 'decision': decision, 'csrf_token': tok})


def connected_repo(tmp_path, project_slug=SLUG):
    p = repo(tmp_path, REPO)
    (p / '.claude').mkdir(exist_ok=True)
    (p / '.claude' / 'auto.json').write_text(
        '{"test_command": "pytest -q", "central": {"enabled": true, "url": "%s", "project": "%s"}}' % (BASE, project_slug),
        encoding='utf-8')
    return p


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def sign_in(tmp_path, central, env, decision='approve', role='developer'):
    """login start -> approve in the browser -> login wait. -> (lines, stored tokens)."""
    p = connected_repo(tmp_path)
    shown = ac.login_start(p, env=env, send=central.send)
    code = re.search(r'type this code: ([A-Z0-9]{4}-[A-Z0-9]{4})', shown[0]).group(1)
    approve(browser(central, 'alok@flexsin.com', role), code, decision)
    stored, clock = [], Clock()
    lines = ac.login_wait(p, env=env, send=central.send, sleep=clock.sleep, now=clock,
                          store=lambda token, e: stored.append(token) or 'the test store')
    return p, lines, stored


@pytest.fixture
def env(tmp_path):
    return {**ME, 'AUTO_RUNNER_CREDENTIALS': str(tmp_path / 'cfg' / 'credentials')}


def test_sign_in_end_to_end(tmp_path, central, env):
    p, lines, [token] = sign_in(tmp_path, central, env)
    assert token.startswith('arp_') and token not in '\n'.join(lines)
    assert lines == ['Signed in as alok@flexsin.com. The token is stored in the test store; it is not shown here.']
    assert not (tmp_path / 'cfg' / ac.PENDING).exists()                 # the device code is gone too
    out, ok = ac.status(p, env={**env, 'AUTO_RUNNER_TOKEN': token}, send=central.send)
    text = '\n'.join(out)
    assert ok and 'CONNECTED (signed in as alok@flexsin.com)' in text and 'personal token from AUTO_RUNNER_TOKEN' in text
    assert token not in text


def test_the_runner_and_init_use_the_personal_token(tmp_path, central, env):
    p, _, [token] = sign_in(tmp_path, central, env)
    cfg = ar.autocfg.load(p)
    transport = ar.central_transport(cfg, {'AUTO_RUNNER_TOKEN': token})
    transport.send = central.send
    assert transport.pull_pending(SLUG) == []
    assert any('?project=acme-web' in url for _, url, _ in central.calls)
    (tmp_path / 'other').mkdir()
    q = repo(tmp_path / 'other', REPO)                                    # a fresh clone: the slug is read from origin
    plan = ai.make_plan(q, {'url': BASE}, env={'AUTO_RUNNER_TOKEN': token}, send=central.send)
    assert not plan['central_problems'] and any('signed in as alok@flexsin.com' in n for n in plan['central_notes'])


def test_denied_in_the_browser(tmp_path, central, env):
    with pytest.raises(ac.LoginError, match='denied'):
        sign_in(tmp_path, central, env, decision='deny')
    assert not (tmp_path / 'cfg' / ac.PENDING).exists()


def test_a_viewer_is_signed_in_but_refused_for_the_project(tmp_path, central, env):
    p, _, [token] = sign_in(tmp_path, central, env, role='viewer')
    out, ok = ac.status(p, env={**env, 'AUTO_RUNNER_TOKEN': token}, send=central.send)
    assert not ok and 'REFUSED' in '\n'.join(out) and 'developer or above' in '\n'.join(out)


def test_nobody_approves_in_time(tmp_path, central, env):
    p = connected_repo(tmp_path)
    ac.login_start(p, env=env, send=central.send)
    clock = Clock()
    with pytest.raises(ac.LoginError, match='10 minutes'):
        ac.login_wait(p, env=env, send=central.send, sleep=clock.sleep, now=lambda: clock.t + 601,
                      store=lambda *a: pytest.fail('stored'))


def test_wait_without_start(tmp_path, env):
    with pytest.raises(ac.LoginError, match='login start first'):
        ac.login_wait(tmp_path, env=env)


def test_start_needs_an_address(tmp_path, env):
    with pytest.raises(ac.LoginError, match='no central service address'):
        ac.login_start(repo(tmp_path), env=env)


# ---------- where the token is kept ----------

@pytest.mark.skipif(sys.platform.startswith('win'), reason='file modes')
def test_the_file_is_readable_only_by_you(tmp_path, env):
    where = ac.store_token('arp_secret', env, platform='linux')
    path = tmp_path / 'cfg' / 'credentials'
    assert str(path) in where and 'arp_secret' not in where
    assert path.read_text(encoding='utf-8') == 'arp_secret\n'
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert safe.read_credentials(env) == 'arp_secret'


def test_windows_sets_the_user_variable_without_the_token_on_a_command_line(env):
    seen = []
    where = ac.store_token('arp_secret', env, platform='win32', run=lambda argv, e: seen.append((argv, e)) or 0)
    [(argv, child)] = seen
    assert 'arp_secret' not in ' '.join(argv) and child['AUTO_RUNNER_NEW_TOKEN'] == 'arp_secret'
    assert "SetEnvironmentVariable('AUTO_RUNNER_TOKEN'" in argv[-1] and 'restart Claude Code' in where


def test_the_token_is_found_in_order(tmp_path, env):
    cfg = {'central': {'enabled': True, 'url': BASE, 'project': SLUG, 'token_env': 'AUTO_RUNNER_TOKEN_ACME_WEB'}}
    assert safe.central_token(cfg, env) == ('', '')
    assert safe.central_token(cfg, {**env, 'AUTO_RUNNER_TOKEN_ACME_WEB': 'art_p'}) == ('art_p', 'AUTO_RUNNER_TOKEN_ACME_WEB')
    ac.store_token('arp_file', env, platform='linux')
    assert safe.central_token(cfg, {**env, 'AUTO_RUNNER_TOKEN_ACME_WEB': 'art_p'})[0] == 'arp_file'
    assert safe.central_token(cfg, {**env, 'AUTO_RUNNER_TOKEN': 'arp_env'}) == ('arp_env', 'AUTO_RUNNER_TOKEN')
    assert safe.central_problems(cfg, env) == []                          # a stored personal token is enough
