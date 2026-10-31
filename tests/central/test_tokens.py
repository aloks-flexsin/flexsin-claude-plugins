import datetime
import hashlib
import re

import pytest

from central import accounts, audit, db, projects, tokens

PW = 'violet-anchor-58-tide'
START = datetime.datetime(2026, 10, 31, 9, 0, tzinfo=datetime.timezone.utc)
MEMBER_ROLES = ('viewer', 'developer', 'manager', 'owner')
TOKEN_RE = re.compile(r'<code id="new-token">(art_[A-Za-z0-9_-]+)</code>')


@pytest.fixture
def api_app(app):
    """The app plus one test API route per scope, guarded by bearer_token, registered before any request."""
    for scope in tokens.SCOPES:
        def view(_scope=scope):
            from flask import g, jsonify
            return jsonify(scope=_scope, project=g.token_project['slug'], token_id=g.token['id'])
        app.add_url_rule('/api/test/%s' % scope, 'api_test_' + scope, tokens.bearer_token(scope)(view),
                         methods=['GET', 'POST'])
    app.config['CLOCK'] = lambda: START
    return app


@pytest.fixture
def conn(api_app, db_url):
    c = db.connect(db_url)
    yield c
    c.close()


def new_client(app):
    c = app.test_client()
    c.environ_base['wsgi.url_scheme'] = 'https'
    return c


def csrf(client, path='/'):
    page = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def post(client, path, **data):
    data['csrf_token'] = csrf(client, '/signin' if client.get_cookie('__Host-session') is None else '/')
    return client.post(path, data=data)


def signed_in(app, email):
    c = new_client(app)
    resp = c.post('/signin', data={'email': email, 'password': PW, 'csrf_token': csrf(c, '/signin')})
    assert resp.status_code == 302
    return c


@pytest.fixture
def world(api_app, conn):
    """Project 'alpha' with one member per role, an admin, a stranger, and project 'beta'."""
    accts = {}
    for who in MEMBER_ROLES + ('stranger',):
        accts[who] = accounts.create(conn, '%s@example.com' % who, PW)
    accts['admin'] = accounts.create(conn, 'admin@example.com', PW, role='admin')
    alpha = projects.create(conn, 'alpha', 'Alpha', '2026-10-31T09:00:00Z')
    beta = projects.create(conn, 'beta', 'Beta', '2026-10-31T09:00:00Z')
    for role in MEMBER_ROLES:
        projects.add_member(conn, alpha['id'], '%s@example.com' % role, role, '2026-10-31T09:00:00Z')
    projects.add_member(conn, beta['id'], 'stranger@example.com', 'owner', '2026-10-31T09:00:00Z')
    clients = {who: signed_in(api_app, '%s@example.com' % who) for who in accts}
    clients['anonymous'] = new_client(api_app)
    return {'app': api_app, 'accounts': accts, 'clients': clients, 'alpha': alpha, 'beta': beta}


def create_token(client, slug='alpha', scopes=('ingest',)):
    resp = post(client, '/projects/%s/tokens' % slug, scope=list(scopes))
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return TOKEN_RE.search(resp.get_data(as_text=True)).group(1), resp


def everything_stored(conn):
    """Every value in every table, as one string."""
    parts = []
    for table in db.TABLES:
        for row in conn.query('SELECT * FROM %s' % table):
            parts.extend(str(v) for v in row.values())
    return '\n'.join(parts)


def api(app, path, raw=None, header=None):
    headers = {}
    if header is not None:
        headers['Authorization'] = header
    elif raw is not None:
        headers['Authorization'] = 'Bearer ' + raw
    return new_client(app).get(path, headers=headers)


# --- the token itself ---

def test_generated_token_has_the_prefix_and_32_random_bytes():
    raw = tokens.generate()
    assert raw.startswith('art_')
    assert len(raw) == len('art_') + 43  # 32 bytes in urlsafe base64 without padding
    assert raw != tokens.generate()


def test_scopes_are_checked():
    assert tokens.normalize_scopes(['pull', 'ingest', 'pull']) == ('ingest', 'pull')
    with pytest.raises(tokens.TokenError):
        tokens.normalize_scopes([])
    with pytest.raises(tokens.TokenError):
        tokens.normalize_scopes(['admin'])


def test_the_database_never_holds_the_raw_token(world, conn):
    raw, _ = create_token(world['clients']['owner'], scopes=('ingest', 'pull'))
    stored = everything_stored(conn)
    assert raw not in stored
    assert raw[len('art_'):] not in stored
    row = conn.query_one('SELECT * FROM token')
    assert row['token_hash'] == hashlib.sha256(raw.encode()).hexdigest()
    assert row['prefix'] == raw[:12] and len(row['prefix']) < len(raw)
    assert row['scopes'] == 'ingest pull'
    assert row['created_by'] == world['accounts']['owner']['id']


def test_the_token_is_shown_once(world):
    client = world['clients']['manager']
    raw, resp = create_token(client)
    assert resp.headers['Cache-Control'] == 'no-store'
    later = client.get('/projects/alpha/tokens')
    assert later.status_code == 200
    page = later.get_data(as_text=True)
    assert raw not in page
    assert raw[:12] in page  # the prefix is shown for recognition
    assert 'new-token' not in page


# --- who may manage tokens ---

@pytest.mark.parametrize('who', MEMBER_ROLES + ('admin', 'stranger', 'anonymous'))
def test_only_managers_owners_and_admins_manage_tokens(world, conn, who):
    client = world['clients'][who]
    allowed = who in ('manager', 'owner', 'admin')
    page = client.get('/projects/alpha/tokens')
    assert page.status_code == (200 if allowed else 404)
    resp = post(client, '/projects/alpha/tokens', scope='ingest')
    assert resp.status_code == (201 if allowed else 404)
    assert conn.query_one('SELECT COUNT(*) AS n FROM token')['n'] == (1 if allowed else 0)


def test_members_page_links_tokens_only_for_managers(world):
    assert 'API tokens' in world['clients']['manager'].get('/projects/alpha').get_data(as_text=True)
    assert 'API tokens' not in world['clients']['developer'].get('/projects/alpha').get_data(as_text=True)


def test_a_token_of_another_project_is_404(world, conn):
    _, _ = create_token(world['clients']['stranger'], slug='beta')
    beta_token = conn.query_one('SELECT id FROM token')['id']
    owner = world['clients']['owner']
    assert post(owner, '/projects/alpha/tokens/%s/revoke' % beta_token).status_code == 404
    assert post(owner, '/projects/alpha/tokens/%s/rotate' % beta_token).status_code == 404
    assert conn.query_one('SELECT revoked_at FROM token')['revoked_at'] is None


def test_no_scope_is_refused_and_audited(world, conn):
    resp = post(world['clients']['owner'], '/projects/alpha/tokens')
    assert resp.status_code == 400
    assert conn.query_one('SELECT COUNT(*) AS n FROM token')['n'] == 0
    assert audit.rows(conn, 'token.create.refused')


# --- revoke and rotate ---

def test_revoke_stops_the_token(world, conn):
    owner = world['clients']['owner']
    raw, _ = create_token(owner)
    assert api(world['app'], '/api/test/ingest', raw).status_code == 200
    token_id = conn.query_one('SELECT id FROM token')['id']
    resp = post(owner, '/projects/alpha/tokens/%s/revoke' % token_id)
    assert resp.status_code == 302
    assert conn.query_one('SELECT revoked_at FROM token')['revoked_at'] == '2026-10-31T09:00:00Z'
    assert api(world['app'], '/api/test/ingest', raw).status_code == 401
    again = post(owner, '/projects/alpha/tokens/%s/revoke' % token_id)
    assert again.status_code == 400
    assert audit.rows(conn, 'token.revoke.refused')


def test_rotate_gives_a_new_token_and_revokes_the_old(world, conn):
    manager = world['clients']['manager']
    old_raw, _ = create_token(manager, scopes=('pull',))
    old_id = conn.query_one('SELECT id FROM token')['id']
    resp = post(manager, '/projects/alpha/tokens/%s/rotate' % old_id)
    assert resp.status_code == 201
    new_raw = TOKEN_RE.search(resp.get_data(as_text=True)).group(1)
    assert new_raw != old_raw
    rows = {r['id']: r for r in conn.query('SELECT * FROM token')}
    assert len(rows) == 2
    assert rows[old_id]['revoked_at'] is not None
    (new_row,) = [r for r in rows.values() if r['id'] != old_id]
    assert new_row['revoked_at'] is None and new_row['scopes'] == 'pull'
    assert api(world['app'], '/api/test/pull', old_raw).status_code == 401
    assert api(world['app'], '/api/test/pull', new_raw).status_code == 200
    assert new_raw not in everything_stored(conn)
    # A revoked token cannot be rotated.
    assert post(manager, '/projects/alpha/tokens/%s/rotate' % old_id).status_code == 400
    assert len(conn.query('SELECT id FROM token')) == 2


def test_rotate_is_all_or_nothing(world, conn, monkeypatch):
    raw, row = tokens.create(conn, world['alpha']['id'], ['ingest'], None, '2026-10-31T09:00:00Z')

    def broken(*_a, **_k):
        raise RuntimeError('insert failed')
    monkeypatch.setattr(tokens, '_insert', broken)
    with pytest.raises(RuntimeError):
        tokens.rotate(conn, world['alpha']['id'], row['id'], None, '2026-10-31T10:00:00Z')
    assert tokens.lookup(conn, raw) is not None


# --- the bearer_token guard ---

def test_bearer_scope_check(world, conn):
    owner = world['clients']['owner']
    ingest_only, _ = create_token(owner, scopes=('ingest',))
    both, _ = create_token(owner, scopes=('ingest', 'pull'))
    app = world['app']
    ok = api(app, '/api/test/ingest', ingest_only)
    assert ok.status_code == 200
    assert ok.get_json()['project'] == 'alpha'
    wrong = api(app, '/api/test/pull', ingest_only)
    assert wrong.status_code == 403
    assert 'pull' in wrong.get_json()['error']
    assert api(app, '/api/test/pull', both).status_code == 200
    assert api(app, '/api/test/ingest', both).status_code == 200


@pytest.mark.parametrize('header', [None, '', 'Bearer', 'Bearer ', 'Basic dXNlcjpwYXNz',
                                    'Bearer art_not-a-real-token', 'Bearer not-even-the-prefix'])
def test_bearer_missing_or_bad_token_is_401(world, header):
    resp = api(world['app'], '/api/test/ingest', header=header) if header is not None \
        else api(world['app'], '/api/test/ingest')
    assert resp.status_code == 401
    assert resp.headers['WWW-Authenticate'].startswith('Bearer')
    assert resp.get_json()['error']


def test_bearer_needs_no_csrf_and_no_session(world):
    raw, _ = create_token(world['clients']['owner'])
    resp = new_client(world['app']).post('/api/test/ingest', headers={'Authorization': 'Bearer ' + raw})
    assert resp.status_code == 200


def test_bearer_guard_rejects_unknown_scope_names():
    with pytest.raises(ValueError):
        tokens.bearer_token('admin')


# --- audit ---

def test_every_action_is_audited_without_the_token(world, conn):
    owner = world['clients']['owner']
    raw, _ = create_token(owner, scopes=('ingest', 'pull'))
    token_id = conn.query_one('SELECT id FROM token')['id']
    rotated = post(owner, '/projects/alpha/tokens/%s/rotate' % token_id)
    new_raw = TOKEN_RE.search(rotated.get_data(as_text=True)).group(1)
    new_id = conn.query_one('SELECT id FROM token WHERE revoked_at IS NULL')['id']
    post(owner, '/projects/alpha/tokens/%s/revoke' % new_id)

    created = audit.rows(conn, 'token.create')
    assert len(created) == 1
    assert created[0]['account_id'] == world['accounts']['owner']['id']
    assert created[0]['project_id'] == world['alpha']['id']
    assert created[0]['detail'] == {'token_id': token_id, 'prefix': raw[:12], 'scopes': 'ingest pull'}
    (rot,) = audit.rows(conn, 'token.rotate')
    assert rot['detail']['old_token_id'] == token_id and rot['detail']['token_id'] == new_id
    (rev,) = audit.rows(conn, 'token.revoke')
    assert rev['detail']['token_id'] == new_id
    text = everything_stored(conn)
    assert raw not in text and new_raw not in text
