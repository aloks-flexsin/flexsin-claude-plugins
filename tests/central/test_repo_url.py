"""A project's repository URL: what is accepted, the edit form, its audit rows, `GET /api/v1/project` and the
column migration for databases made before the column existed."""
import json

import pytest

from central import accounts, audit, db, projects, tokens

from .test_access import PW, new_client, post, signed_in

AT = '2026-11-02T09:00:00Z'
REPO = 'https://github.com/example/alpha.git'


@pytest.mark.parametrize('url', [
    'https://github.com/example/alpha.git',
    'https://github.com/example/alpha',
    'https://gitlab.example.com:8443/group/sub/alpha.git',
    'git@github.com:example/alpha.git',
    'git@github.com:example/alpha',
    'ssh://git@github.com/example/alpha.git',
    'ssh://git@git.example.com:2222/example/alpha.git',
    'ssh://github.com/example/alpha',
])
def test_accepted_repo_urls(url):
    assert projects.normalize_repo_url('  %s ' % url) == url


@pytest.mark.parametrize('url', [
    '', '   ',
    'http://github.com/example/alpha.git',                      # not https
    'file:///srv/git/alpha.git', '/srv/git/alpha.git', 'C:\\repos\\alpha',
    'https://user:secret@github.com/example/alpha.git',         # a password
    'https://ghp_abcdef123456@github.com/example/alpha.git',    # a token as the user
    'https://user@github.com/example/alpha.git',                # any user in an https URL
    'ssh://git:secret@github.com/example/alpha.git',
    'git:secret@github.com:example/alpha.git',
    'https://github.com/alpha',                                  # no owner
    'git@github.com:alpha',
    'https://github.com/example/alpha.git?x=1', 'https://github.com/example/alpha.git#main',
    'https://github.com/example/../alpha', 'https://github.com/example/al pha',
    'https://github.com:0/example/alpha', 'https://github.com:99999/example/alpha',
    'https://' + 'a' * 500 + '.com/example/alpha',
    'ext::sh -c touch% /tmp/pwned', 'git@-oProxyCommand=x:example/alpha',
])
def test_refused_repo_urls(url):
    with pytest.raises(projects.ProjectError):
        projects.normalize_repo_url(url)


def test_create_needs_a_repo_url(conn):
    with pytest.raises(projects.ProjectError):
        projects.create(conn, 'gamma', 'Gamma', AT, '')
    with pytest.raises(TypeError):
        projects.create(conn, 'gamma', 'Gamma', AT)  # noqa: the argument is required
    assert projects.get_by_slug(conn, 'gamma') is None


@pytest.fixture
def conn(app, db_url):
    c = db.connect(db_url)
    yield c
    c.close()


@pytest.fixture
def world(app, conn):
    accts = {who: accounts.create(conn, '%s@example.com' % who, PW) for who in ('owner', 'manager', 'viewer')}
    accts['admin'] = accounts.create(conn, 'admin@example.com', PW, role='admin')
    alpha = projects.create(conn, 'alpha', 'Alpha', AT, REPO)
    for role in ('owner', 'manager', 'viewer'):
        projects.add_member(conn, alpha['id'], '%s@example.com' % role, role, AT)
    return {'alpha': alpha, 'accounts': accts, 'clients': {who: signed_in(app, '%s@example.com' % who) for who in accts}}


def test_members_page_shows_the_repo_url_and_the_form_only_to_owners(world):
    for who in ('owner', 'admin', 'manager', 'viewer'):
        page = world['clients'][who].get('/projects/alpha').get_data(as_text=True)
        assert REPO in page
        assert ('/projects/alpha/repo' in page) == (who in ('owner', 'admin'))


def test_admin_create_form_asks_for_the_repo_url(world):
    page = world['clients']['admin'].get('/projects').get_data(as_text=True)
    assert 'name="repo_url"' in page


def test_owner_and_admin_change_the_repo_url_and_it_is_audited(world, conn):
    new = 'git@github.com:example/alpha-renamed.git'
    resp = post(world['clients']['owner'], '/projects/alpha/repo', repo_url=new)
    assert resp.status_code == 302
    assert projects.get_by_slug(conn, 'alpha')['repo_url'] == new
    [row] = audit.rows(conn, 'project.repo_url')
    assert row['account_id'] == world['accounts']['owner']['id'] and row['project_id'] == world['alpha']['id']
    assert row['detail'] == {'from': REPO, 'to': new}
    assert post(world['clients']['admin'], '/projects/alpha/repo', repo_url=REPO).status_code == 302
    assert projects.get_by_slug(conn, 'alpha')['repo_url'] == REPO


@pytest.mark.parametrize('who', ['manager', 'viewer'])
def test_others_get_404_on_the_repo_form(world, conn, who):
    assert post(world['clients'][who], '/projects/alpha/repo', repo_url='git@github.com:x/y.git').status_code == 404
    assert projects.get_by_slug(conn, 'alpha')['repo_url'] == REPO
    assert audit.rows(conn, 'project.repo_url') == []


def test_a_refused_repo_url_is_audited_without_the_value(world, conn):
    bad = 'https://someone:hunter2-secret@github.com/example/alpha.git'
    resp = post(world['clients']['owner'], '/projects/alpha/repo', repo_url=bad)
    assert resp.status_code == 400 and 'role="alert"' in resp.get_data(as_text=True)
    assert 'hunter2' not in resp.get_data(as_text=True)
    assert projects.get_by_slug(conn, 'alpha')['repo_url'] == REPO
    [row] = audit.rows(conn, 'project.repo_url.refused')
    assert 'hunter2' not in json.dumps(row['detail'])


def test_create_refuses_a_credential_and_does_not_echo_it(world, conn):
    resp = post(world['clients']['admin'], '/projects', slug='gamma', name='Gamma',
                repo_url='https://x:hunter2-secret@github.com/example/gamma.git')
    assert resp.status_code == 400 and 'hunter2' not in resp.get_data(as_text=True)
    assert projects.get_by_slug(conn, 'gamma') is None


# --- GET /api/v1/project ---

def _get(app, raw=None):
    headers = {'Authorization': 'Bearer ' + raw} if raw else {}
    return new_client(app).get('/api/v1/project', headers=headers)


@pytest.mark.parametrize('scopes', [('ingest',), ('pull',), ('ingest', 'pull')])
def test_api_project_answers_for_any_scope(app, world, conn, scopes):
    raw, _ = tokens.create(conn, world['alpha']['id'], scopes, world['accounts']['owner']['id'], AT)
    resp = _get(app, raw)
    assert resp.status_code == 200
    assert resp.get_json() == {'slug': 'alpha', 'name': 'Alpha', 'repo_url': REPO}


def test_api_project_needs_a_valid_active_token(app, world, conn):
    assert _get(app).status_code == 401
    assert _get(app, 'art_not-a-real-token').status_code == 401
    raw, row = tokens.create(conn, world['alpha']['id'], ['pull'], None, AT)
    tokens.revoke(conn, world['alpha']['id'], row['id'], AT)
    assert _get(app, raw).status_code == 401


def test_api_project_says_when_the_repo_url_is_not_set(app, world, conn):
    conn.execute("UPDATE project SET repo_url = NULL WHERE id = ?", (world['alpha']['id'],))
    conn.commit()
    raw, _ = tokens.create(conn, world['alpha']['id'], ['pull'], None, AT)
    assert _get(app, raw).get_json()['repo_url'] is None


# --- migration ---

OLD_PROJECT = "CREATE TABLE project (id VARCHAR(64) PRIMARY KEY, slug VARCHAR(64) NOT NULL UNIQUE," \
              " name VARCHAR(200) NOT NULL, created_at VARCHAR(32) NOT NULL)"


def test_init_db_adds_the_column_to_an_older_database(db_url):
    old = db.connect(db_url)
    old.execute(OLD_PROJECT)
    old.execute("INSERT INTO project (id, slug, name, created_at) VALUES ('p1', 'old', 'Old', ?)", (AT,))
    old.commit()
    old.close()
    assert db.init_db(db_url) == ['project.repo_url']
    assert db.init_db(db_url) == []          # a second run changes nothing
    conn = db.connect(db_url)
    try:
        assert 'repo_url' in conn.columns('project')
        assert projects.get_by_slug(conn, 'old')['repo_url'] is None
        projects.set_repo_url(conn, 'p1', REPO)
        assert projects.get_by_slug(conn, 'old')['repo_url'] == REPO
    finally:
        conn.close()


def test_init_db_cli_reports_the_added_column(db_url):
    import io
    from central import cli
    old = db.connect(db_url)
    old.execute(OLD_PROJECT)
    old.commit()
    old.close()
    out = io.StringIO()
    assert cli.main(['init-db'], environ={'DATABASE_URL': db_url}, out=out, err=io.StringIO()) == 0
    assert 'added column project.repo_url' in out.getvalue()


def test_schema_and_columns_added_agree(db_url):
    db.init_db(db_url)
    conn = db.connect(db_url)
    try:
        for table, column, _ in db.COLUMNS_ADDED:
            assert column in conn.columns(table)
    finally:
        conn.close()
