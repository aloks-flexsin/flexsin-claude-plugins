import datetime
import re

import pytest

from central import accounts, audit, auth, db, passwords

ADMIN_PW = 'violet-anchor-58-tide'
USER_PW = 'copper-lantern-91-moss'
NEW_PW = 'amber-harbour-42-fern'
START = datetime.datetime(2026, 10, 31, 9, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def clock(app):
    app.config['CLOCK'] = lambda: START


@pytest.fixture
def conn(app, db_url):
    c = db.connect(db_url)
    yield c
    c.close()


@pytest.fixture
def root(conn):
    return accounts.create(conn, 'root@example.com', ADMIN_PW, role='admin')


@pytest.fixture
def bob(conn):
    return accounts.create(conn, 'bob@example.com', USER_PW)


def new_client(app):
    c = app.test_client()
    c.environ_base['wsgi.url_scheme'] = 'https'
    return c


def token(client, path):
    page = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def signin(client, email, password):
    return client.post('/signin', data={'email': email, 'password': password, 'csrf_token': token(client, '/signin')})


def post(client, path, **data):
    data['csrf_token'] = token(client, '/')
    return client.post(path, data=data)


@pytest.fixture
def as_root(app, root):
    c = new_client(app)
    assert signin(c, 'root@example.com', ADMIN_PW).status_code == 302
    return c


@pytest.fixture
def as_bob(app, bob):
    c = new_client(app)
    assert signin(c, 'bob@example.com', USER_PW).status_code == 302
    return c


def admin_routes(app):
    out = []
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith('/admin'):
            path = rule.rule.replace('<account_id>', 'x')
            for method in rule.methods & {'GET', 'POST'}:
                out.append((method, path))
    return out


def test_non_admins_and_strangers_get_404_on_every_admin_page(app, conn, as_bob, bob, root):
    routes = admin_routes(app)
    assert ('GET', '/admin/accounts') in routes and len(routes) >= 5
    anon = new_client(app)
    for method, path in routes:
        path = path.replace('/x/', '/%s/' % root['id'])
        if method == 'GET':
            assert as_bob.get(path).status_code == 404, path
            assert anon.get(path).status_code == 404, path
        else:
            assert post(as_bob, path, role='admin', disabled='1', password=NEW_PW,
                        email='eve@example.com').status_code == 404, path
    root_now = accounts.get(conn, root['id'])
    assert root_now['role'] == 'admin' and not root_now['disabled']
    assert accounts.find_by_email(conn, 'eve@example.com') is None


def test_home_links_accounts_only_for_admins(as_root, as_bob):
    assert '/admin/accounts' in as_root.get('/').get_data(as_text=True)
    assert '/admin/accounts' not in as_bob.get('/').get_data(as_text=True)


def test_admin_adds_an_account_that_can_sign_in(app, conn, as_root, root):
    resp = post(as_root, '/admin/accounts', email='Carol@Example.com', role='user', password=NEW_PW)
    assert resp.status_code == 302
    carol = accounts.find_by_email(conn, 'carol@example.com')
    assert carol['role'] == 'user'
    assert 'carol@example.com' in as_root.get('/admin/accounts').get_data(as_text=True)
    assert signin(new_client(app), 'carol@example.com', NEW_PW).status_code == 302
    [row] = audit.rows(conn, 'account.create')
    assert row['account_id'] == root['id']
    assert row['detail'] == {'target_id': carol['id'], 'email': 'carol@example.com', 'role': 'user'}


@pytest.mark.parametrize('data', [
    {'email': 'carol@example.com', 'role': 'user', 'password': 'short'},
    {'email': 'carol@example.com', 'role': 'user', 'password': 'password1234'},
    {'email': 'root@EXAMPLE.com', 'role': 'user', 'password': NEW_PW},
    {'email': 'carol@example.com', 'role': 'owner', 'password': NEW_PW},
    {'email': 'not-an-email', 'role': 'user', 'password': NEW_PW},
])
def test_add_refuses_bad_input(conn, as_root, data):
    resp = post(as_root, '/admin/accounts', **data)
    assert resp.status_code == 400 and 'role="alert"' in resp.get_data(as_text=True)
    assert len(accounts.list_all(conn)) == 1
    assert audit.rows(conn, 'account.create') == []


def test_disabled_account_cannot_sign_in_and_loses_its_sessions(app, conn, as_root, as_bob, bob, root):
    assert post(as_root, '/admin/accounts/%s/disabled' % bob['id'], disabled='1').status_code == 302
    assert accounts.get(conn, bob['id'])['disabled'] == 1
    assert conn.query('SELECT * FROM session WHERE account_id = ?', (bob['id'],)) == []
    assert as_bob.get('/').status_code == 302
    assert signin(new_client(app), 'bob@example.com', USER_PW).status_code == 401
    assert post(as_root, '/admin/accounts/%s/disabled' % bob['id'], disabled='0').status_code == 302
    assert signin(new_client(app), 'bob@example.com', USER_PW).status_code == 302
    assert [r['detail'] for r in audit.rows(conn, 'account.disable')] == [
        {'target_id': bob['id'], 'email': 'bob@example.com'}]
    assert [r['account_id'] for r in audit.rows(conn, 'account.enable')] == [root['id']]


def test_change_role_is_audited_and_takes_effect(conn, as_root, as_bob, bob):
    assert as_bob.get('/admin/accounts').status_code == 404
    assert post(as_root, '/admin/accounts/%s/role' % bob['id'], role='admin').status_code == 302
    assert as_bob.get('/admin/accounts').status_code == 200
    assert post(as_root, '/admin/accounts/%s/role' % bob['id'], role='user').status_code == 302
    assert as_bob.get('/admin/accounts').status_code == 404
    details = [r['detail'] for r in audit.rows(conn, 'account.role')]
    assert [(d['from'], d['to']) for d in details] == [('user', 'admin'), ('admin', 'user')]
    assert post(as_root, '/admin/accounts/%s/role' % bob['id'], role='owner').status_code == 400


def test_last_enabled_admin_cannot_be_disabled_or_demoted(conn, as_root, root, bob):
    resp = post(as_root, '/admin/accounts/%s/disabled' % root['id'], disabled='1')
    assert resp.status_code == 400 and 'last enabled admin' in resp.get_data(as_text=True)
    resp = post(as_root, '/admin/accounts/%s/role' % root['id'], role='user')
    assert resp.status_code == 400
    now = accounts.get(conn, root['id'])
    assert now['role'] == 'admin' and not now['disabled']
    assert as_root.get('/admin/accounts').status_code == 200
    refused = [r['detail']['reason'] for r in audit.rows(conn) if r['action'].endswith('.refused')]
    assert len(refused) == 2
    assert audit.rows(conn, 'account.disable') == [] and audit.rows(conn, 'account.role') == []


def test_a_disabled_admin_does_not_count_towards_the_guard(conn, root, bob):
    accounts.set_role(conn, bob['id'], 'admin')
    accounts.set_disabled(conn, bob['id'], True)
    with pytest.raises(accounts.AccountError):
        accounts.set_disabled(conn, root['id'], True)
    with pytest.raises(accounts.AccountError):
        accounts.set_role(conn, root['id'], 'user')
    # With a second enabled admin, either may step down, but not both.
    accounts.set_disabled(conn, bob['id'], False)
    accounts.set_role(conn, root['id'], 'user')
    with pytest.raises(accounts.AccountError):
        accounts.set_disabled(conn, bob['id'], True)
    assert accounts.get(conn, bob['id'])['role'] == 'admin'


def test_an_admin_may_step_down_when_another_admin_remains(conn, as_root, root, bob):
    accounts.set_role(conn, bob['id'], 'admin')
    assert post(as_root, '/admin/accounts/%s/role' % root['id'], role='user').status_code == 302
    assert as_root.get('/admin/accounts').status_code == 404


def test_reset_password_signs_the_person_out_of_every_session(app, conn, as_root, as_bob, bob, root):
    second = new_client(app)
    signin(second, 'bob@example.com', USER_PW)
    assert len(conn.query('SELECT * FROM session WHERE account_id = ?', (bob['id'],))) == 2
    assert post(as_root, '/admin/accounts/%s/password' % bob['id'], password=NEW_PW).status_code == 302
    assert conn.query('SELECT * FROM session WHERE account_id = ?', (bob['id'],)) == []
    assert as_bob.get('/').status_code == 302 and second.get('/').status_code == 302
    assert as_root.get('/').status_code == 200  # the admin's own session is untouched
    assert signin(new_client(app), 'bob@example.com', USER_PW).status_code == 401
    assert signin(new_client(app), 'bob@example.com', NEW_PW).status_code == 302
    [row] = audit.rows(conn, 'account.password_reset')
    assert row['account_id'] == root['id'] and row['detail'] == {'target_id': bob['id'], 'email': 'bob@example.com'}


def test_reset_clears_a_lockout(app, conn, as_root, bob):
    for _ in range(auth.MAX_FAILURES):
        signin(new_client(app), 'bob@example.com', 'wrong-wrong-wrong-1')
    assert accounts.get(conn, bob['id'])['locked_until']
    post(as_root, '/admin/accounts/%s/password' % bob['id'], password=NEW_PW)
    assert signin(new_client(app), 'bob@example.com', NEW_PW).status_code == 302


def test_reset_refuses_a_weak_password(conn, as_root, bob):
    before = accounts.get(conn, bob['id'])['password_hash']
    assert post(as_root, '/admin/accounts/%s/password' % bob['id'], password='short').status_code == 400
    assert accounts.get(conn, bob['id'])['password_hash'] == before
    assert audit.rows(conn, 'account.password_reset') == []


def test_unknown_account_gets_404(as_root):
    for suffix, data in (('disabled', {'disabled': '1'}), ('role', {'role': 'user'}), ('password', {'password': NEW_PW})):
        assert post(as_root, '/admin/accounts/nope/' + suffix, **data).status_code == 404


def test_own_password_change_needs_the_current_password(app, conn, as_bob, bob):
    resp = post(as_bob, '/account/password', current_password='wrong-wrong-wrong-1', new_password=NEW_PW,
                confirm_password=NEW_PW)
    assert resp.status_code == 400
    assert passwords.verify_password(USER_PW, accounts.get(conn, bob['id'])['password_hash'])
    resp = post(as_bob, '/account/password', current_password=USER_PW, new_password=NEW_PW,
                confirm_password=NEW_PW + 'x')
    assert resp.status_code == 400
    resp = post(as_bob, '/account/password', current_password=USER_PW, new_password='short',
                confirm_password='short')
    assert resp.status_code == 400
    reasons = [r['detail']['reason'] for r in audit.rows(conn, 'account.password_change.refused')]
    assert reasons == ['wrong_current_password', 'mismatch', 'policy']
    assert as_bob.get('/').status_code == 200  # refused changes leave the session alone


def test_own_password_change_signs_out_every_session(app, conn, as_bob, bob):
    second = new_client(app)
    signin(second, 'bob@example.com', USER_PW)
    resp = post(as_bob, '/account/password', current_password=USER_PW, new_password=NEW_PW,
                confirm_password=NEW_PW)
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/signin')
    assert conn.query('SELECT * FROM session WHERE account_id = ?', (bob['id'],)) == []
    assert as_bob.get_cookie(auth.SESSION_COOKIE) is None
    assert second.get('/').status_code == 302
    assert signin(new_client(app), 'bob@example.com', NEW_PW).status_code == 302
    [row] = audit.rows(conn, 'account.password_change')
    assert row['account_id'] == bob['id'] and row['detail'] == {'email': 'bob@example.com'}


def test_own_password_page_needs_a_session(app):
    resp = new_client(app).get('/account/password')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/signin')


def test_admin_forms_carry_csrf_and_refuse_without_it(conn, as_root, bob):
    page = as_root.get('/admin/accounts').get_data(as_text=True)
    assert page.count('<form') == page.count('name="csrf_token"') >= 4
    own = as_root.get('/account/password').get_data(as_text=True)
    assert own.count('<form') == own.count('name="csrf_token"')
    assert as_root.post('/admin/accounts/%s/disabled' % bob['id'], data={'disabled': '1'}).status_code == 400
    assert as_root.post('/admin/accounts', data={'email': 'x@example.com', 'password': NEW_PW}).status_code == 400
    assert not accounts.get(conn, bob['id'])['disabled']


def test_no_password_reaches_the_audit_table(conn, as_root, as_bob, bob):
    post(as_root, '/admin/accounts', email='carol@example.com', role='user', password=NEW_PW)
    post(as_root, '/admin/accounts/%s/password' % bob['id'], password=NEW_PW)
    raw = str(conn.query('SELECT * FROM audit'))
    for secret in (ADMIN_PW, USER_PW, NEW_PW):
        assert secret not in raw
