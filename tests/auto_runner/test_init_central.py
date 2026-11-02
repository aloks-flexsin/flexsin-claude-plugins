"""auto-runner plugin: init connects a project to the central service (central is the default; --local opts out).
Requests go to the real central app through its Flask test client: the HTTP binding is real, no socket is opened."""
import json
import sys

import pytest

from tests.auto_runner.test_init import ai, make_repo, sh

ac = ai.autocentral
BASE = 'https://central.test'
SLUG = 'acme-web'
REPO = 'https://github.com/Example/acme-web.git'
ENV = 'AUTO_RUNNER_TOKEN_ACME_WEB'
AT = '2026-11-02T09:00:00Z'


class Central:
    """The real central app on SQLite with project `acme-web` (repository REPO) and `other`, a token for each."""

    def __init__(self, root):
        auto_runner = str(ai.HERE.parent)
        if auto_runner not in sys.path:
            sys.path.insert(0, auto_runner)
        import central
        from central import db, projects, tokens
        url = 'sqlite:///' + (root / 'central.sqlite3').as_posix()
        db.init_db(url)
        self.client = central.create_app({'TESTING': True, 'DATABASE_URL': url}).test_client()
        self.conn = db.connect(url)
        self.project = projects.create(self.conn, SLUG, 'Acme web', AT, REPO)
        other = projects.create(self.conn, 'other', 'Other', AT, 'git@github.com:example/other.git')
        self.token, _ = tokens.create(self.conn, self.project['id'], ['pull'], None, AT)
        self.other_token, _ = tokens.create(self.conn, other['id'], ['ingest'], None, AT)
        self.calls, self.down = [], False

    def send(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers.get('Authorization')))
        if self.down:
            raise ConnectionRefusedError('no route')
        assert url.startswith(BASE + '/')
        r = self.client.open(url[len(BASE):], method=method, headers=headers, data=body, base_url=BASE)
        return r.status_code, r.get_data()


@pytest.fixture
def central(tmp_path):
    c = Central(tmp_path)
    yield c
    c.conn.close()


def repo(tmp_path, origin=None):
    p = make_repo(tmp_path)
    if origin:
        sh(p, 'remote', 'add', 'origin', origin)
    return p


def plan(p, central, env=None, **opts):
    opts.setdefault('url', BASE)
    opts.setdefault('project', SLUG)
    return ai.make_plan(p, opts, env=env if env is not None else {}, send=central.send)


def auto_json(p):
    return json.loads((p / '.claude' / 'auto.json').read_text(encoding='utf-8'))


# ---------- comparing repository URLs ----------

@pytest.mark.parametrize('url', [
    'https://github.com/example/acme-web.git', 'https://github.com/Example/Acme-Web', 'https://github.com/example/acme-web/',
    'git@github.com:example/acme-web.git', 'git@github.com:example/acme-web',
    'ssh://git@github.com/example/acme-web.git', 'ssh://git@github.com:22/example/acme-web',
    'https://x-access-token:ghp_secret@github.com/example/acme-web.git',
])
def test_the_same_repository_in_any_form_has_one_key(url):
    assert ac.repo_key(url) == 'github.com/example/acme-web'


@pytest.mark.parametrize('url', ['', 'not a url', 'file:///srv/acme.git', '/srv/acme.git', 'https://github.com/acme',
                                 'ext::sh -c x', 'https://github.com/example/acme?x=1'])
def test_other_forms_have_no_key(url):
    assert ac.repo_key(url) is None


def test_a_different_repository_has_a_different_key():
    assert ac.repo_key('git@github.com:example/acme-web.git') != ac.repo_key('https://github.com/example/acme-api')
    assert ac.repo_key('https://gitlab.com/example/acme-web') != ac.repo_key('https://github.com/example/acme-web')


def test_clean_url_drops_credentials():
    assert ac.clean_url('https://x:ghp_secret@github.com/e/r.git') == 'https://github.com/e/r.git'
    assert ac.clean_url('git@github.com:e/r.git') == 'git@github.com:e/r.git'


def test_default_token_env():
    assert ac.default_token_env('acme-web') == ENV
    assert ac.default_token_env('flexsin-claude-plugins') == 'AUTO_RUNNER_TOKEN_FLEXSIN_CLAUDE_PLUGINS'


# ---------- central is the default ----------

def test_without_central_options_or_local_init_refuses(tmp_path, capsys):
    p = repo(tmp_path)
    assert ai.main(['--project', str(p), '--apply']) == 1
    out = capsys.readouterr().out
    assert '--central-url' in out and '--central-project' in out and '--local' in out
    assert not (p / '.claude').exists() and not (p / '.auto').exists()


def test_local_writes_no_central_section(tmp_path, capsys):
    p = repo(tmp_path)
    assert ai.main(['--project', str(p), '--local', '--apply']) == 0
    assert 'central' not in auto_json(p)
    assert 'Local mode' in capsys.readouterr().out


def test_without_the_token_init_writes_the_section_and_says_what_to_do(tmp_path, central):
    p = repo(tmp_path, REPO)
    pl = plan(p, central)
    assert pl['central_problems'] == [] and central.calls == []          # no token: nothing asked
    text = '\n'.join(ai.describe(pl))
    assert f'{BASE}/projects/{SLUG}/tokens' in text and 'ingest and pull' in text
    assert f"Read-Host 'Token for {SLUG}' -AsSecureString" in text and f"'{ENV}'" in text
    assert REPO in text                                                   # the repository to give the new project
    ai.apply(pl)
    assert auto_json(p)['central'] == {'enabled': True, 'url': BASE, 'project': SLUG, 'token_env': ENV}


def test_the_steps_never_show_a_credential_from_origin(tmp_path, central):
    p = repo(tmp_path, 'https://x-access-token:ghp_secret123@github.com/example/acme-web.git')
    assert 'ghp_secret123' not in '\n'.join(ai.describe(plan(p, central)))


@pytest.mark.parametrize('origin', [REPO, 'git@github.com:example/acme-web.git', 'https://github.com/example/acme-web'])
def test_with_the_token_a_matching_origin_passes(tmp_path, central, origin):
    p = repo(tmp_path, origin)
    pl = plan(p, central, env={ENV: central.token})
    assert pl['central_problems'] == []
    assert central.calls == [('GET', BASE + '/api/v1/project', 'Bearer ' + central.token)]
    assert 'origin is its repository' in '\n'.join(ai.describe(pl))
    assert central.token not in '\n'.join(ai.describe(pl))
    ai.apply(pl)
    assert auto_json(p)['central']['project'] == SLUG


def test_a_different_origin_is_refused_and_nothing_is_written(tmp_path, central, capsys):
    p = repo(tmp_path, 'git@github.com:example/someone-else.git')
    pl = plan(p, central, env={ENV: central.token})
    assert len(pl['central_problems']) == 1 and 'is not project "acme-web"' in pl['central_problems'][0]
    assert 'Nothing was written.' in ai.describe(pl)
    with pytest.raises(SystemExit):
        ai.apply(pl)
    assert not (p / '.claude').exists()


def test_main_refuses_with_exit_1(tmp_path, central, monkeypatch, capsys):
    p = repo(tmp_path, 'git@github.com:example/someone-else.git')
    monkeypatch.setenv(ENV, central.token)
    monkeypatch.setattr(ac.autodeliver, 'urllib_send', central.send)
    assert ai.main(['--project', str(p), '--central-url', BASE, '--central-project', SLUG, '--apply']) == 1
    assert 'REFUSED' in capsys.readouterr().out and not (p / '.claude').exists()


def test_no_origin_is_refused(tmp_path, central):
    p = repo(tmp_path)
    [problem] = plan(p, central, env={ENV: central.token})['central_problems']
    assert 'no "origin" remote' in problem


def test_a_token_of_another_project_is_refused(tmp_path, central):
    p = repo(tmp_path, REPO)
    [problem] = plan(p, central, env={ENV: central.other_token})['central_problems']
    assert 'belongs to project "other"' in problem


def test_a_project_without_a_repo_url_is_refused(tmp_path, central):
    central.conn.execute('UPDATE project SET repo_url = NULL WHERE id = ?', (central.project['id'],))
    central.conn.commit()
    p = repo(tmp_path, REPO)
    [problem] = plan(p, central, env={ENV: central.token})['central_problems']
    assert 'no repository URL' in problem


def test_a_bad_token_is_refused_without_printing_it(tmp_path, central):
    p = repo(tmp_path, REPO)
    [problem] = plan(p, central, env={ENV: 'art_not-a-real-token'})['central_problems']
    assert '401' in problem and 'art_not-a-real-token' not in problem


def test_an_unreachable_service_is_refused(tmp_path, central):
    central.down = True
    p = repo(tmp_path, REPO)
    [problem] = plan(p, central, env={ENV: central.token})['central_problems']
    assert 'could not be reached' in problem


@pytest.mark.parametrize('opts,needle', [
    ({'url': 'http://central.example.com'}, '--central-url'),
    ({'url': 'https://user:pw@central.example.com'}, '--central-url'),
    ({'project': 'Not A Slug!'}, '--central-project'),
    ({'token_env': 'art_abcdefgh'}, 'NAME of an environment variable'),
    ({'token_env': 'has space'}, '--token-env'),
])
def test_bad_options_are_refused(tmp_path, central, opts, needle):
    p = repo(tmp_path, REPO)
    problems = plan(p, central, **opts)['central_problems']
    assert problems and needle in problems[0]


def test_http_is_allowed_for_localhost(tmp_path, central):
    p = repo(tmp_path, REPO)
    assert plan(p, central, url='http://localhost:5000')['central_problems'] == []


def test_a_named_token_env_is_kept(tmp_path, central):
    p = repo(tmp_path, REPO)
    ai.apply(plan(p, central, token_env='MY_TOKEN'))
    assert auto_json(p)['central']['token_env'] == 'MY_TOKEN'


# ---------- an existing auto.json ----------

def test_central_is_added_to_an_existing_auto_json_and_removed_again(tmp_path, central):
    p = repo(tmp_path, REPO)
    (p / '.claude').mkdir()
    (p / '.claude' / 'auto.json').write_text(json.dumps({'test_command': 'make test'}), encoding='utf-8')
    pl = plan(p, central)
    assert '.claude/auto.json: add "central"' in '\n'.join(ai.describe(pl))
    ai.apply(pl)
    assert auto_json(p) == {'test_command': 'make test',
                            'central': {'enabled': True, 'url': BASE, 'project': SLUG, 'token_env': ENV}}
    actions, _ = ai.remove_plan(p)
    assert ('central', '"central" in .claude/auto.json') in actions
    ai.remove(p)
    assert auto_json(p) == {'test_command': 'make test'}


def test_an_edited_central_section_survives_remove(tmp_path, central):
    p = repo(tmp_path, REPO)
    (p / '.claude').mkdir()
    (p / '.claude' / 'auto.json').write_text(json.dumps({'test_command': 'make test'}), encoding='utf-8')
    ai.apply(plan(p, central))
    data = auto_json(p)
    data['central']['token_env'] = 'MINE'
    (p / '.claude' / 'auto.json').write_text(json.dumps(data), encoding='utf-8')
    ai.remove(p)
    assert auto_json(p)['central']['token_env'] == 'MINE'


def test_an_existing_central_section_is_left_alone_and_checked(tmp_path, central):
    p = repo(tmp_path, 'git@github.com:example/someone-else.git')
    (p / '.claude').mkdir()
    section = {'enabled': True, 'url': BASE, 'project': SLUG, 'token_env': ENV}
    (p / '.claude' / 'auto.json').write_text(json.dumps({'test_command': 'x', 'central': section}), encoding='utf-8')
    pl = ai.make_plan(p, None, env={ENV: central.token}, send=central.send)   # no options needed
    assert pl['central_add'] is None and 'is not project "acme-web"' in pl['central_problems'][0]
    pl = ai.make_plan(p, None, env={}, send=central.send)
    assert pl['central_problems'] == [] and auto_json(p)['central'] == section


def test_an_existing_central_section_that_is_off_needs_no_options(tmp_path, central):
    p = repo(tmp_path)
    (p / '.claude').mkdir()
    (p / '.claude' / 'auto.json').write_text(json.dumps({'test_command': 'x', 'central': {'enabled': False}}),
                                             encoding='utf-8')
    pl = ai.make_plan(p, None, env={}, send=central.send)
    assert pl['central_problems'] == [] and central.calls == []
