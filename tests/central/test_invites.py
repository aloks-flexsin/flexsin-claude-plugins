"""Central service: invites from a project's members page and one-time links (decision record 0008). An email without an
account gets one with no password, the membership and a link; the link works once, for 72 hours, and only the newest one."""
import datetime
import re

import pytest

from central import accounts, audit, db, invites, projects

from .test_access import PW, new_client, post, signed_in, token
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)

NEW_PW = 'amber-harbour-42-fern'
LINK = re.compile(r'<code>(?:https?://[^/<]+)?(/invite/[A-Za-z0-9_-]+)</code>')


@pytest.fixture
def clock(app):
    class Clock:
        now = datetime.datetime(2026, 11, 23, 9, 0, tzinfo=datetime.timezone.utc)

        def __call__(self):
            return self.now
    c = Clock()
    app.config['CLOCK'] = c
    return c


def link_in(resp):
    m = LINK.search(resp.get_data(as_text=True))
    assert m, 'no one-time link on the page'
    return m.group(1)


def use(app, path, password=NEW_PW, again=None):
    c = new_client(app)
    csrf = token(c, '/signin')               # the pre-session form token; a dead link's page has no form to read it from
    return c, c.post(path, data={'password': password, 'password_again': again or password, 'csrf_token': csrf})


def test_an_owner_invites_an_email_without_an_account(app, world, conn, clock):
    owner = world['clients']['owner']
    resp = post(owner, '/projects/alpha/members', email='alok.sharma.connect@gmail.com', role='developer')
    assert resp.status_code == 200 and 'Invited alok.sharma.connect@gmail.com' in resp.get_data(as_text=True)
    path = link_in(resp)
    new = accounts.find_by_email(conn, 'alok.sharma.connect@gmail.com')
    assert new['password_hash'] == '!' and new['role'] == 'user'
    assert projects.member_role(conn, world['alpha']['id'], new['id']) == 'developer'
    page = owner.get('/projects/alpha').get_data(as_text=True)
    assert 'invited' in page and 'New invite link' in page and path not in page          # the link is shown once
    assert new_client(app).get(path).status_code == 200
    c, done = use(app, path)
    assert done.status_code == 302 and c.get('/projects/alpha').status_code == 200       # signed in, a member
    assert 'invited' not in owner.get('/projects/alpha').get_data(as_text=True)
    assert len(audit.rows(conn, 'invite.create')) == 1 and len(audit.rows(conn, 'invite.accept')) == 1
    assert path.rsplit('/', 1)[1] not in str(audit.rows(conn))


def test_an_existing_account_is_just_added(world, conn):
    accounts.create(conn, 'dev@example.com', PW)
    resp = post(world['clients']['owner'], '/projects/alpha/members', email='dev@example.com', role='developer')
    assert resp.status_code == 302 and audit.rows(conn, 'invite.create') == []


def test_a_link_works_once(app, world, clock):
    path = link_in(post(world['clients']['owner'], '/projects/alpha/members', email='a@example.com', role='viewer'))
    assert use(app, path)[1].status_code == 302
    assert use(app, path)[1].status_code == 404
    assert new_client(app).get(path).status_code == 404


def test_a_link_expires_after_72_hours(app, world, clock):
    path = link_in(post(world['clients']['owner'], '/projects/alpha/members', email='a@example.com', role='viewer'))
    clock.now += datetime.timedelta(hours=72)
    assert new_client(app).get(path).status_code == 404 and use(app, path)[1].status_code == 404


def test_a_new_link_replaces_the_old_one(app, world, conn, clock):
    owner = world['clients']['owner']
    old = link_in(post(owner, '/projects/alpha/members', email='a@example.com', role='viewer'))
    member = accounts.find_by_email(conn, 'a@example.com')
    new = link_in(post(owner, '/projects/alpha/members/%s/invite' % member['id']))
    assert old != new and use(app, old)[1].status_code == 404 and use(app, new)[1].status_code == 302


def test_no_new_invite_link_for_someone_with_a_password(world):
    viewer_id = world['accounts']['viewer']['id']
    resp = post(world['clients']['owner'], '/projects/alpha/members/%s/invite' % viewer_id)
    assert resp.status_code == 400 and 'reset link' in resp.get_data(as_text=True)


def test_mismatched_or_weak_passwords_keep_the_link(app, world, conn, clock):
    path = link_in(post(world['clients']['owner'], '/projects/alpha/members', email='a@example.com', role='viewer'))
    assert use(app, path, NEW_PW, NEW_PW + 'x')[1].status_code == 400
    assert use(app, path, 'short')[1].status_code == 400
    assert accounts.find_by_email(conn, 'a@example.com')['password_hash'] == '!'
    assert use(app, path)[1].status_code == 302


def test_an_invited_account_cannot_sign_in_before_setting_a_password(app, world, conn):
    post(world['clients']['owner'], '/projects/alpha/members', email='a@example.com', role='viewer')
    c = new_client(app)
    resp = c.post('/signin', data={'email': 'a@example.com', 'password': '!', 'csrf_token': token(c, '/signin')})
    assert resp.status_code == 401


def test_a_disabled_account_cannot_use_its_link(app, world, conn, clock):
    path = link_in(post(world['clients']['owner'], '/projects/alpha/members', email='a@example.com', role='viewer'))
    accounts.set_disabled(conn, accounts.find_by_email(conn, 'a@example.com')['id'], True)
    assert use(app, path)[1].status_code == 404


def test_only_owners_and_admins_invite(world, conn):
    for who in ('viewer', 'manager'):
        assert post(world['clients'][who], '/projects/alpha/members', email='x@example.com', role='viewer').status_code == 404
    assert accounts.find_by_email(conn, 'x@example.com') is None
    assert post(world['clients']['admin'], '/projects/alpha/members', email='x@example.com', role='viewer').status_code == 200


def test_the_link_uses_public_url_when_set(app, world):
    app.config['PUBLIC_URL'] = 'https://central.example.com/'
    resp = post(world['clients']['owner'], '/projects/alpha/members', email='a@example.com', role='viewer')
    assert '<code>https://central.example.com/invite/' in resp.get_data(as_text=True)


def test_unknown_links_get_404(app):
    for path in ('/invite/nope', '/invite/' + 'x' * 200):
        assert new_client(app).get(path).status_code == 404


def test_only_the_hash_is_stored(app, world, conn):
    path = link_in(post(world['clients']['owner'], '/projects/alpha/members', email='a@example.com', role='viewer'))
    raw = path.rsplit('/', 1)[1]
    [row] = conn.query('SELECT * FROM invite')
    assert raw not in str(dict(row)) and row['token_hash'] == invites._hash(raw)


def test_init_db_adds_the_invite_table_to_an_existing_database(db_url):
    db.init_db(db_url)
    c = db.connect(db_url)
    c.execute('DROP TABLE invite')
    c.commit()
    c.close()
    db.init_db(db_url)
    c = db.connect(db_url)
    try:
        assert 'token_hash' in c.columns('invite')
    finally:
        c.close()
