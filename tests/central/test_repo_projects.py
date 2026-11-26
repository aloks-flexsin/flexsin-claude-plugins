"""Central service: one project per repository. The slug and name are filled in from the repository URL, a second
project for the same repository (in any URL form) is refused, and init-db fills `repo_key` and makes it unique, stopping
without a change when two projects already share a repository."""
import importlib.util
import io
from pathlib import Path

import pytest

from central import cli, db, projects

from .test_access import post
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)

BIN = Path(__file__).resolve().parents[2] / 'auto-runner' / 'bin'
FORMS = ['https://github.com/Example/Case-Study-VA.git', 'https://github.com/example/case-study-va',
         'ssh://git@github.com/example/case-study-va.git', 'git@github.com:Example/Case-Study-VA.git',
         'ssh://git@github.com:22/example/case-study-va']


@pytest.mark.parametrize('url', FORMS)
def test_every_form_of_one_repository_has_one_key(url):
    assert projects.repo_key(url) == 'github.com/example/case-study-va'
    assert projects.slug_from_repo(url) == 'case-study-va'


@pytest.mark.parametrize('url', ['', 'C:/repos/x', 'file:///x/y', 'https://github.com/only-owner', 'ftp://h/o/r'])
def test_no_key_for_what_is_not_a_repository(url):
    assert projects.repo_key(url) is None and projects.slug_from_repo(url) is None


def test_the_plugin_and_the_service_agree():
    spec = importlib.util.spec_from_file_location('autorunner_central_parity', BIN / 'autocentral.py')
    ac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ac)
    for url in FORMS + ['git@gitlab.example.com:group/sub/My_Repo.git', 'https://bitbucket.org/o/r.git', 'x', '']:
        assert ac.repo_key(url) == projects.repo_key(url), url
        assert ac.slug_from_repo(url) == projects.slug_from_repo(url), url


def test_slug_and_name_come_from_the_repository(app, conn):
    p = projects.create(conn, '', '', AT, FORMS[0])
    assert (p['slug'], p['name'], p['repo_key']) == ('case-study-va', 'case-study-va', 'github.com/example/case-study-va')


def test_a_taken_slug_becomes_owner_repo(app, conn):
    projects.create(conn, 'case-study-va', 'Other', AT, 'https://github.com/someone/else.git')
    p = projects.create(conn, '', 'Case study', AT, FORMS[0])
    assert p['slug'] == 'example-case-study-va' and p['name'] == 'Case study'


@pytest.mark.parametrize('second', FORMS[1:])
def test_a_second_project_for_the_same_repository_is_refused(app, conn, second):
    projects.create(conn, '', '', AT, FORMS[0])
    with pytest.raises(projects.ProjectError, match='already belongs to project "case-study-va"'):
        projects.create(conn, 'another', 'Another', AT, second)
    assert len(conn.query('SELECT * FROM project')) == 1


def test_changing_the_repository_to_a_taken_one_is_refused(app, conn):
    a = projects.create(conn, '', '', AT, FORMS[0])
    b = projects.create(conn, '', '', AT, 'https://github.com/example/other.git')
    with pytest.raises(projects.ProjectError, match='already belongs to project'):
        projects.set_repo_url(conn, b['id'], FORMS[3])
    projects.set_repo_url(conn, a['id'], FORMS[3])                 # the same repository in another form is fine
    assert projects.get_by_slug(conn, 'other')['repo_key'] == 'github.com/example/other'


def test_the_form_fills_in_the_slug_and_refuses_a_taken_repository(world, conn):
    admin = world['clients']['admin']
    resp = post(admin, '/projects', slug='', name='', repo_url=FORMS[0])
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/projects/case-study-va/tasks')
    resp = post(admin, '/projects', slug='', name='', repo_url=FORMS[3])
    assert resp.status_code == 400 and 'already belongs to project' in resp.get_data(as_text=True)


# ---------- init-db on an existing database ----------

def _old(db_url, rows):
    """A database from before repo_key, with these (slug, repo_url) projects."""
    c = db.connect(db_url)
    c.execute("CREATE TABLE project (id VARCHAR(64) PRIMARY KEY, slug VARCHAR(64) NOT NULL UNIQUE, name VARCHAR(200) "
              "NOT NULL, repo_url VARCHAR(500), created_at VARCHAR(32) NOT NULL)")
    for i, (slug, url) in enumerate(rows):
        c.execute("INSERT INTO project (id, slug, name, repo_url, created_at) VALUES (?, ?, ?, ?, ?)",
                  ('p%d' % i, slug, slug, url, AT))
    c.commit()
    c.close()


def test_init_db_fills_the_keys_and_makes_them_unique(db_url):
    _old(db_url, [('a', FORMS[0]), ('b', 'https://github.com/example/b.git'), ('c', None)])
    assert db.init_db(db_url) == ['project.repo_key', 'project.share_frictions', 'project.autonomy']
    c = db.connect(db_url)
    try:
        assert {r['slug']: r['repo_key'] for r in c.query('SELECT slug, repo_key FROM project')} == {
            'a': 'github.com/example/case-study-va', 'b': 'github.com/example/b', 'c': None}
        with pytest.raises(Exception):
            c.execute("INSERT INTO project (id, slug, name, repo_url, repo_key, created_at) VALUES "
                      "('x', 'x', 'x', 'u', 'github.com/example/b', ?)", (AT,))
    finally:
        c.close()
    assert db.init_db(db_url) == []                                  # a second run changes nothing


def test_init_db_stops_without_a_change_when_two_projects_share_a_repository(db_url):
    _old(db_url, [('one', FORMS[0]), ('two', FORMS[3]), ('ok', 'https://github.com/example/ok.git')])
    with pytest.raises(db.DuplicateRepositories) as e:
        db.init_db(db_url)
    assert e.value.args[0] == {'github.com/example/case-study-va': ['one', 'two']}
    c = db.connect(db_url)
    try:
        assert 'repo_key' not in c.columns('project')                # nothing was committed
    finally:
        c.close()
    err = io.StringIO()
    assert cli.main(['init-db'], environ={'DATABASE_URL': db_url}, out=io.StringIO(), err=err) == 3
    assert 'github.com/example/case-study-va: one, two' in err.getvalue() and 'nothing changed' in err.getvalue()
