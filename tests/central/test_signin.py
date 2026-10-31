import datetime
import hashlib
import logging
import re

import pytest

from central import accounts, audit, auth, db, sessions

GOOD = 'violet-anchor-58-tide'
WRONG = 'copper-lantern-91-moss'
START = datetime.datetime(2026, 10, 31, 9, 0, tzinfo=datetime.timezone.utc)


class Clock:
    def __init__(self, at=START):
        self.at = at

    def __call__(self):
        return self.at

    def advance(self, **delta):
        self.at += datetime.timedelta(**delta)


@pytest.fixture
def clock(app):
    c = Clock()
    app.config['CLOCK'] = c
    return c


@pytest.fixture
def conn(app, db_url):
    c = db.connect(db_url)
    yield c
    c.close()


@pytest.fixture
def ada(conn):
    return accounts.create(conn, 'ada@example.com', GOOD)


def _csrf(client, path='/signin'):
    page = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def signin(client, email='ada@example.com', password=GOOD):
    return client.post('/signin', data={'email': email, 'password': password, 'csrf_token': _csrf(client)})


def _set_cookie_headers(response, name):
    return [h for h in response.headers.getlist('Set-Cookie') if h.startswith(name + '=')]


def test_signin_sets_a_secure_httponly_lax_cookie_holding_only_a_hashed_id(client, conn, ada, clock):
    resp = signin(client)
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/')
    [header] = _set_cookie_headers(resp, auth.SESSION_COOKIE)
    flags = [part.strip().lower() for part in header.split(';')]
    assert 'secure' in flags and 'httponly' in flags and 'samesite=lax' in flags and 'path=/' in flags
    raw = client.get_cookie(auth.SESSION_COOKIE).value
    rows = conn.query('SELECT * FROM session')
    assert len(rows) == 1
    assert rows[0]['id_hash'] == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in str(rows[0])
    home = client.get('/')
    assert home.status_code == 200 and 'ada@example.com' in home.get_data(as_text=True)


def test_csrf_cookie_has_the_same_flags(client):
    resp = client.get('/signin')
    [header] = _set_cookie_headers(resp, auth.CSRF_COOKIE)
    flags = [part.strip().lower() for part in header.split(';')]
    assert 'secure' in flags and 'httponly' in flags and 'samesite=lax' in flags


def test_home_needs_a_session(client):
    resp = client.get('/')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/signin')


def test_a_new_session_id_at_every_signin(client, conn, ada, clock):
    signin(client)
    first = client.get_cookie(auth.SESSION_COOKIE).value
    # Signing in again while signed in (the form token is now the session's).
    resp = client.post('/signin', data={'email': 'ada@example.com', 'password': GOOD,
                                        'csrf_token': _csrf(client, '/')})
    assert resp.status_code == 302
    second = client.get_cookie(auth.SESSION_COOKIE).value
    assert first != second
    hashes = [r['id_hash'] for r in conn.query('SELECT id_hash FROM session')]
    assert hashes == [sessions.hash_id(second)]  # the old session ended
    client.set_cookie(auth.SESSION_COOKIE, first, secure=True)
    assert client.get('/').status_code == 302  # the old id no longer signs anyone in


def test_a_session_id_planted_before_signin_is_not_kept(client, conn, ada, clock):
    client.set_cookie(auth.SESSION_COOKIE, 'attacker-chosen', secure=True)
    signin(client)
    assert client.get_cookie(auth.SESSION_COOKIE).value != 'attacker-chosen'


def test_idle_expiry_after_two_hours(client, conn, ada, clock):
    signin(client)
    clock.advance(hours=1, minutes=59)
    assert client.get('/').status_code == 200  # activity moves the idle window
    clock.advance(hours=1, minutes=59)
    assert client.get('/').status_code == 200
    clock.advance(hours=2)
    assert client.get('/').status_code == 302
    assert conn.query('SELECT * FROM session') == []


def test_absolute_expiry_after_twelve_hours_even_when_active(client, conn, ada, clock):
    signin(client)
    for _ in range(11):
        clock.advance(hours=1)
        assert client.get('/').status_code == 200
    clock.advance(minutes=59)
    assert client.get('/').status_code == 200
    clock.advance(minutes=1)
    resp = client.get('/')
    assert resp.status_code == 302
    assert conn.query('SELECT * FROM session') == []
    assert client.get_cookie(auth.SESSION_COOKIE) is None


def test_signout_ends_the_session(client, conn, ada, clock):
    signin(client)
    raw = client.get_cookie(auth.SESSION_COOKIE).value
    resp = client.post('/signout', data={'csrf_token': _csrf(client, '/')})
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/signin')
    assert conn.query('SELECT * FROM session') == []
    client.set_cookie(auth.SESSION_COOKIE, raw, secure=True)
    assert client.get('/').status_code == 302
    assert [r['detail']['email'] for r in audit.rows(conn, 'signout')] == ['ada@example.com']


def test_disabled_account_loses_its_session_and_cannot_sign_in(client, conn, ada, clock):
    signin(client)
    accounts.set_disabled(conn, ada['id'], True)
    assert client.get('/').status_code == 302
    assert signin(client).status_code == 401
    assert audit.rows(conn, 'signin.failure')[-1]['detail']['reason'] == 'disabled'


def test_unknown_email_gets_the_same_answer_as_a_wrong_password(client, ada, clock):
    wrong = signin(client, password=WRONG)
    unknown = signin(client, email='nobody@example.com', password=WRONG)
    assert wrong.status_code == unknown.status_code == 401

    def message(resp):
        return re.search(r'<p role="alert">(.*?)</p>', resp.get_data(as_text=True)).group(1)
    assert message(wrong) == message(unknown)
    assert client.get_cookie(auth.SESSION_COOKIE) is None


def test_lockout_after_five_failures_and_unlock_after_fifteen_minutes(client, conn, ada, clock):
    for _ in range(4):
        assert signin(client, password=WRONG).status_code == 401
    assert accounts.get(conn, ada['id'])['locked_until'] is None
    assert signin(client, password=WRONG).status_code == 401
    assert accounts.get(conn, ada['id'])['locked_until'] == '2026-10-31T09:15:00Z'
    # Locked: even the right password is refused, with the same answer.
    locked = signin(client)
    assert locked.status_code == 401 and auth.FAILED_MESSAGE in locked.get_data(as_text=True)
    clock.advance(minutes=14, seconds=59)
    assert signin(client).status_code == 401
    clock.advance(seconds=1)
    assert signin(client).status_code == 302
    acc = accounts.get(conn, ada['id'])
    assert acc['failed_count'] == 0 and acc['locked_until'] is None
    reasons = [r['detail']['reason'] for r in audit.rows(conn, 'signin.failure')]
    assert reasons == ['wrong_password'] * 5 + ['locked', 'locked']


def test_a_success_resets_the_failure_count(client, conn, ada, clock):
    for _ in range(4):
        signin(client, password=WRONG)
    assert signin(client).status_code == 302
    assert accounts.get(conn, ada['id'])['failed_count'] == 0
    client.post('/signout', data={'csrf_token': _csrf(client, '/')})
    for _ in range(4):
        signin(client, password=WRONG)
    assert accounts.get(conn, ada['id'])['locked_until'] is None


def test_after_unlock_the_count_starts_again(client, conn, ada, clock):
    for _ in range(5):
        signin(client, password=WRONG)
    clock.advance(minutes=15)
    assert signin(client, password=WRONG).status_code == 401
    acc = accounts.get(conn, ada['id'])
    assert acc['locked_until'] is None and acc['failed_count'] == 1


@pytest.mark.parametrize('data', [
    {'email': 'ada@example.com', 'password': GOOD},
    {'email': 'ada@example.com', 'password': GOOD, 'csrf_token': ''},
    {'email': 'ada@example.com', 'password': GOOD, 'csrf_token': 'forged'},
])
def test_signin_without_a_valid_csrf_token_is_refused(client, conn, ada, clock, data):
    client.get('/signin')
    resp = client.post('/signin', data=data)
    assert resp.status_code == 400
    assert conn.query('SELECT * FROM session') == []
    assert audit.rows(conn) == []


def test_signin_csrf_needs_the_cookie(app, conn, ada, clock):
    a = app.test_client()
    a.environ_base['wsgi.url_scheme'] = 'https'
    token = _csrf(a)
    b = app.test_client()
    b.environ_base['wsgi.url_scheme'] = 'https'
    resp = b.post('/signin', data={'email': 'ada@example.com', 'password': GOOD, 'csrf_token': token})
    assert resp.status_code == 400


def test_signed_in_posts_need_the_session_token(client, conn, ada, clock):
    anon_token = _csrf(client)
    signin(client)
    assert client.post('/signout').status_code == 400
    assert client.post('/signout', data={'csrf_token': anon_token}).status_code == 400
    assert len(conn.query('SELECT * FROM session')) == 1
    session_token = conn.query_one('SELECT csrf_token FROM session')['csrf_token']
    assert _csrf(client, '/') == session_token
    assert client.post('/signout', data={'csrf_token': session_token}).status_code == 302


def test_every_post_route_checks_csrf(app, client):
    posts = [r.rule for r in app.url_map.iter_rules() if 'POST' in r.methods and not r.rule.startswith('/api/')]
    assert posts
    for path in posts:
        assert client.post(path, data={}).status_code == 400, path


def test_audit_rows_for_success_and_failure_never_hold_the_password(client, conn, ada, clock, caplog):
    caplog.set_level(logging.DEBUG)
    signin(client, password=WRONG)
    signin(client, email='Nobody@Example.com', password=WRONG)
    signin(client)
    ok = audit.rows(conn, 'signin.success')
    failed = audit.rows(conn, 'signin.failure')
    assert [(r['account_id'], r['detail']) for r in ok] == [(ada['id'], {'email': 'ada@example.com'})]
    assert [(r['account_id'], r['detail']) for r in failed] == [
        (ada['id'], {'email': 'ada@example.com', 'reason': 'wrong_password'}),
        (None, {'email': 'nobody@example.com', 'reason': 'unknown_email'}),
    ]
    raw_audit = str(conn.query('SELECT * FROM audit'))
    logs = caplog.text
    for secret in (GOOD, WRONG):
        assert secret not in raw_audit
        assert secret not in logs
    assert 'ada@example.com' in logs  # the log did record the attempts


def test_security_headers_on_the_signin_page(client):
    resp = client.get('/signin')
    assert "frame-ancestors 'none'" in resp.headers['Content-Security-Policy']
    assert 'style=' not in resp.get_data(as_text=True) and '<script' not in resp.get_data(as_text=True)


def test_healthz_still_works_without_a_session(client):
    assert client.get('/healthz').status_code == 200
