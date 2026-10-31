import datetime
import re

import pytest

from central import access, accounts, audit, db, projects

PW = 'violet-anchor-58-tide'
START = datetime.datetime(2026, 10, 31, 9, 0, tzinfo=datetime.timezone.utc)
MEMBER_ROLES = ('viewer', 'developer', 'manager', 'owner')
WHO = MEMBER_ROLES + ('admin', 'stranger', 'anonymous')


@pytest.fixture
def guarded_app(app):
    """The app plus one test route per min_role, guarded by project_access, registered before any request."""
    for min_role in MEMBER_ROLES:
        def view(slug, _min=min_role):
            from flask import g
            return '%s:%s:%s' % (_min, g.project['slug'], g.project_role)
        app.add_url_rule('/test-guard/%s/<slug>' % min_role, 'guard_' + min_role,
                         access.project_access(min_role)(view))
    app.config['CLOCK'] = lambda: START
    return app


@pytest.fixture
def conn(guarded_app, db_url):
    c = db.connect(db_url)
    yield c
    c.close()


def new_client(app):
    c = app.test_client()
    c.environ_base['wsgi.url_scheme'] = 'https'
    return c


def token(client, path):
    page = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def post(client, path, **data):
    data['csrf_token'] = token(client, '/signin') if client.get_cookie('__Host-session') is None else token(client, '/')
    return client.post(path, data=data)


def signed_in(app, email):
    c = new_client(app)
    resp = c.post('/signin', data={'email': email, 'password': PW, 'csrf_token': token(c, '/signin')})
    assert resp.status_code == 302
    return c


@pytest.fixture
def world(guarded_app, conn):
    """Project 'alpha' with one member per role, an admin, and a stranger who is a member of 'beta' only."""
    app = guarded_app
    accts = {}
    for who in MEMBER_ROLES + ('stranger',):
        accts[who] = accounts.create(conn, '%s@example.com' % who, PW)
    accts['admin'] = accounts.create(conn, 'admin@example.com', PW, role='admin')
    alpha = projects.create(conn, 'alpha', 'Alpha', '2026-10-31T09:00:00Z')
    beta = projects.create(conn, 'beta', 'Beta', '2026-10-31T09:00:00Z')
    for role in MEMBER_ROLES:
        projects.add_member(conn, alpha['id'], '%s@example.com' % role, role, '2026-10-31T09:00:00Z')
    projects.add_member(conn, beta['id'], 'stranger@example.com', 'owner', '2026-10-31T09:00:00Z')
    clients = {who: signed_in(app, '%s@example.com' % who) for who in accts}
    clients['anonymous'] = new_client(app)
    return {'app': app, 'accounts': accts, 'clients': clients, 'alpha': alpha, 'beta': beta}


EXPECTED_ROLE = {'viewer': 'viewer', 'developer': 'developer', 'manager': 'manager', 'owner': 'owner', 'admin': 'admin'}


@pytest.mark.parametrize('who', WHO)
@pytest.mark.parametrize('min_role', MEMBER_ROLES)
def test_role_matrix_of_the_guard(world, who, min_role):
    resp = world['clients'][who].get('/test-guard/%s/alpha' % min_role)
    rank = projects.RANK
    allowed = who == 'admin' or (who in rank and rank[who] >= rank[min_role])
    if allowed:
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == '%s:alpha:%s' % (min_role, EXPECTED_ROLE[who])
    else:
        assert resp.status_code == 404, (who, min_role)


def test_unknown_project_and_unknown_role(world):
    for who in WHO:
        assert world['clients'][who].get('/test-guard/viewer/nope').status_code == 404
    with pytest.raises(ValueError):
        access.project_access('admin')
    with pytest.raises(ValueError):
        projects.allows('owner', 'superuser')


@pytest.mark.parametrize('who', WHO)
def test_members_page_and_management_by_role(world, conn, who):
    c = world['clients'][who]
    page = c.get('/projects/alpha')
    if who in ('stranger', 'anonymous'):
        assert page.status_code == 404
    else:
        assert page.status_code == 200
        text = page.get_data(as_text=True)
        assert 'owner@example.com' in text and 'viewer@example.com' in text
        assert ('Add member' in text) == (who in ('owner', 'admin'))
    if who == 'anonymous':
        return
    resp = post(c, '/projects/alpha/members', email='stranger@example.com', role='viewer')
    if who in ('owner', 'admin'):
        assert resp.status_code == 302
        assert projects.member_role(conn, world['alpha']['id'], world['accounts']['stranger']['id']) == 'viewer'
    else:
        assert resp.status_code == 404
        assert projects.member_role(conn, world['alpha']['id'], world['accounts']['stranger']['id']) is None
    viewer_id = world['accounts']['viewer']['id']
    resp = post(c, '/projects/alpha/members/%s/role' % viewer_id, role='developer')
    assert resp.status_code == (302 if who in ('owner', 'admin') else 404)
    resp = post(c, '/projects/alpha/members/%s/remove' % viewer_id)
    assert resp.status_code == (302 if who in ('owner', 'admin') else 404)
    gone = projects.member_role(conn, world['alpha']['id'], viewer_id) is None
    assert gone == (who in ('owner', 'admin'))


def test_a_stranger_gets_404_on_every_page_of_another_project(world):
    stranger = world['clients']['stranger']
    rules = [r for r in world['app'].url_map.iter_rules() if r.rule.startswith('/projects/<slug>')]
    assert len(rules) >= 4
    member_id = world['accounts']['owner']['id']
    for rule in rules:
        path = rule.rule.replace('<slug>', 'alpha').replace('<account_id>', member_id)
        if 'GET' in rule.methods:
            assert stranger.get(path).status_code == 404, path
        if 'POST' in rule.methods:
            assert post(stranger, path, email='stranger@example.com', role='owner').status_code == 404, path
    # The same answer as for a project that does not exist: nothing reveals that 'alpha' is there.
    assert stranger.get('/projects/alpha').get_data() == stranger.get('/projects/no-such').get_data()
    # Their own project is fine.
    assert stranger.get('/projects/beta').status_code == 200


def test_project_list_shows_members_their_projects_and_admins_all(world):
    clients = world['clients']
    admin_list = clients['admin'].get('/projects').get_data(as_text=True)
    assert 'alpha' in admin_list and 'beta' in admin_list and 'Create project' in admin_list
    viewer_list = clients['viewer'].get('/projects').get_data(as_text=True)
    assert 'alpha' in viewer_list and 'beta' not in viewer_list and 'Create project' not in viewer_list
    stranger_list = clients['stranger'].get('/projects').get_data(as_text=True)
    assert 'beta' in stranger_list and '/projects/alpha' not in stranger_list
    resp = clients['anonymous'].get('/projects')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/signin')


def test_admin_sees_a_project_it_is_not_a_member_of(world, conn):
    assert projects.member_role(conn, world['beta']['id'], world['accounts']['admin']['id']) is None
    assert world['clients']['admin'].get('/projects/beta').status_code == 200


def test_disabled_admin_and_member_lose_access(world, conn):
    accounts.set_disabled(conn, world['accounts']['owner']['id'], True)
    assert projects.effective_role(conn, world['alpha'], accounts.get(conn, world['accounts']['owner']['id'])) is None
    assert world['clients']['owner'].get('/projects/alpha').status_code == 404


def test_only_admins_create_projects(world, conn):
    for who in MEMBER_ROLES + ('stranger',):
        assert post(world['clients'][who], '/projects', slug='gamma', name='Gamma').status_code == 404
    assert projects.get_by_slug(conn, 'gamma') is None
    resp = post(world['clients']['admin'], '/projects', slug='Gamma', name='Gamma')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/projects/gamma')
    gamma = projects.get_by_slug(conn, 'gamma')
    [row] = audit.rows(conn, 'project.create')
    assert row['account_id'] == world['accounts']['admin']['id'] and row['project_id'] == gamma['id']
    assert row['detail'] == {'slug': 'gamma', 'name': 'Gamma'}


@pytest.mark.parametrize('slug,name', [('gamma', ''), ('-bad', 'X'), ('has space', 'X'), ('alpha', 'Dup'),
                                       ('a' * 65, 'X'), ('ok', 'n' * 201)])
def test_create_refuses_bad_input(world, conn, slug, name):
    resp = post(world['clients']['admin'], '/projects', slug=slug, name=name)
    assert resp.status_code == 400 and 'role="alert"' in resp.get_data(as_text=True)
    assert len(conn.query('SELECT * FROM project')) == 2
    assert audit.rows(conn, 'project.create') == []


def test_membership_changes_are_audited(world, conn):
    owner = world['clients']['owner']
    alpha_id = world['alpha']['id']
    owner_id = world['accounts']['owner']['id']
    stranger = world['accounts']['stranger']
    assert post(owner, '/projects/alpha/members', email='STRANGER@example.com', role='developer').status_code == 302
    assert post(owner, '/projects/alpha/members/%s/role' % stranger['id'], role='manager').status_code == 302
    assert post(owner, '/projects/alpha/members/%s/remove' % stranger['id']).status_code == 302
    [add] = audit.rows(conn, 'member.add')
    [role] = audit.rows(conn, 'member.role')
    [remove] = audit.rows(conn, 'member.remove')
    for row in (add, role, remove):
        assert row['account_id'] == owner_id and row['project_id'] == alpha_id
    assert add['detail'] == {'target_id': stranger['id'], 'email': 'stranger@example.com', 'role': 'developer'}
    assert role['detail'] == {'target_id': stranger['id'], 'email': 'stranger@example.com',
                              'from': 'developer', 'to': 'manager'}
    assert remove['detail'] == {'target_id': stranger['id'], 'email': 'stranger@example.com', 'role': 'manager'}


def test_bad_membership_requests_are_refused_and_audited(world, conn):
    owner = world['clients']['owner']
    viewer_id = world['accounts']['viewer']['id']
    assert post(owner, '/projects/alpha/members', email='nobody@example.com', role='viewer').status_code == 400
    assert post(owner, '/projects/alpha/members', email='viewer@example.com', role='viewer').status_code == 400
    assert post(owner, '/projects/alpha/members', email='stranger@example.com', role='admin').status_code == 400
    assert post(owner, '/projects/alpha/members/%s/role' % viewer_id, role='admin').status_code == 400
    assert post(owner, '/projects/alpha/members/nope/role', role='viewer').status_code == 404
    assert post(owner, '/projects/alpha/members/nope/remove').status_code == 404
    # A non-member of alpha (the stranger) cannot be re-roled through alpha.
    assert post(owner, '/projects/alpha/members/%s/remove' % world['accounts']['stranger']['id']).status_code == 404
    assert len(audit.rows(conn, 'member.add.refused')) == 3
    assert len(audit.rows(conn, 'member.role.refused')) == 1
    assert audit.rows(conn, 'member.add') == [] and audit.rows(conn, 'member.role') == []


def test_last_owner_cannot_be_removed_or_demoted(world, conn):
    alpha_id = world['alpha']['id']
    owner_id = world['accounts']['owner']['id']
    admin = world['clients']['admin']
    resp = post(admin, '/projects/alpha/members/%s/role' % owner_id, role='manager')
    assert resp.status_code == 400 and 'last owner' in resp.get_data(as_text=True)
    assert post(admin, '/projects/alpha/members/%s/remove' % owner_id).status_code == 400
    assert projects.member_role(conn, alpha_id, owner_id) == 'owner'
    assert len(audit.rows(conn, 'member.role.refused')) == 1
    assert len(audit.rows(conn, 'member.remove.refused')) == 1
    # With a second owner, the first may step down.
    assert post(admin, '/projects/alpha/members/%s/role' % world['accounts']['manager']['id'],
                role='owner').status_code == 302
    assert post(world['clients']['owner'], '/projects/alpha/members/%s/remove' % owner_id).status_code == 302
    assert world['clients']['owner'].get('/projects/alpha').status_code == 404


def test_membership_forms_carry_csrf_and_refuse_without_it(world, conn):
    page = world['clients']['owner'].get('/projects/alpha').get_data(as_text=True)
    assert page.count('<form') == page.count('name="csrf_token"') >= 9
    viewer_id = world['accounts']['viewer']['id']
    resp = world['clients']['owner'].post('/projects/alpha/members/%s/remove' % viewer_id)
    assert resp.status_code == 400
    assert projects.member_role(conn, world['alpha']['id'], viewer_id) == 'viewer'


def test_home_links_to_projects(world):
    assert '/projects' in world['clients']['viewer'].get('/').get_data(as_text=True)
