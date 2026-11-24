"""Central service: browser push subscriptions and the Account page's push section (queue item N3)."""
import json
import re

import pytest

from central import config, db

from .test_access import new_client, token
from .test_repo_url import AT, OLD_PROJECT, conn, world  # noqa: F401  (fixtures)

KEYS = {'CENTRAL_VAPID_PUBLIC_KEY': 'BPublicKey_abc-123', 'CENTRAL_VAPID_PRIVATE_KEY': 'private-key',
        'CENTRAL_VAPID_SUBJECT': 'mailto:ops@example.com'}
ENDPOINT = 'https://push.example.com/send/abc123'


def sub(endpoint=ENDPOINT, p256dh='BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM',
        auth='tBHItJI5svbpez7KI4CCXg'):
    return {'endpoint': endpoint, 'expirationTime': None, 'keys': {'p256dh': p256dh, 'auth': auth}}


@pytest.fixture
def keys(app):
    app.config.update(KEYS)
    return KEYS


def send(client, path, body, csrf=None, raw=None):
    headers = {'Content-Type': 'application/json', 'User-Agent': 'TestBrowser/1.0'}
    if csrf is not None:
        headers['X-CSRF-Token'] = csrf
    return client.post(path, data=raw if raw is not None else json.dumps(body), headers=headers)


def csrf_of(client):
    return token(client, '/account')


def rows(conn):
    return conn.query('SELECT * FROM push_subscription ORDER BY created_at, id')


def test_a_signed_in_json_post_with_the_right_header_passes_and_a_wrong_one_gets_400(world, conn, keys):
    c = world['clients']['owner']
    resp = send(c, '/push/subscribe', sub(), csrf=csrf_of(c))
    assert resp.status_code == 200 and resp.get_json() == {'ok': True, 'result': 'created'}
    assert send(c, '/push/subscribe', sub('https://push.example.com/other'), csrf='wrong-token').status_code == 400
    assert len(rows(conn)) == 1


def test_missing_csrf_gets_400(world, conn, keys):
    c = world['clients']['owner']
    assert send(c, '/push/subscribe', sub()).status_code == 400
    assert send(c, '/push/unsubscribe', {'endpoint': ENDPOINT}).status_code == 400
    assert rows(conn) == []


def test_another_sessions_token_is_refused(world, conn, keys):
    other = csrf_of(world['clients']['manager'])
    assert send(world['clients']['owner'], '/push/subscribe', sub(), csrf=other).status_code == 400


def test_subscribe_stores_one_row_per_endpoint_and_a_second_subscribe_updates_it(world, conn, keys):
    c = world['clients']['owner']
    csrf = csrf_of(c)
    send(c, '/push/subscribe', sub(), csrf=csrf)
    resp = send(c, '/push/subscribe', sub(p256dh='BNewKey', auth='newAuth'), csrf=csrf)
    assert resp.get_json() == {'ok': True, 'result': 'updated'}
    [row] = rows(conn)
    owner_id = world['accounts']['owner']['id']
    assert (row['account_id'], row['endpoint'], row['p256dh'], row['auth']) == (owner_id, ENDPOINT, 'BNewKey', 'newAuth')
    assert row['user_agent'] == 'TestBrowser/1.0' and row['created_at'] and row['failures'] == 0
    assert row['last_ok_at'] is None
    send(c, '/push/subscribe', sub('https://push.example.com/second'), csrf=csrf)
    assert len(rows(conn)) == 2


def test_the_same_browser_subscribed_by_another_person_becomes_theirs(world, conn, keys):
    send(world['clients']['owner'], '/push/subscribe', sub(), csrf=csrf_of(world['clients']['owner']))
    send(world['clients']['manager'], '/push/subscribe', sub(), csrf=csrf_of(world['clients']['manager']))
    [row] = rows(conn)
    assert row['account_id'] == world['accounts']['manager']['id']


def test_unsubscribe_deletes_only_your_own(world, conn, keys):
    owner, manager = world['clients']['owner'], world['clients']['manager']
    send(owner, '/push/subscribe', sub(), csrf=csrf_of(owner))
    send(manager, '/push/subscribe', sub('https://push.example.com/mine'), csrf=csrf_of(manager))
    resp = send(manager, '/push/unsubscribe', {'endpoint': ENDPOINT}, csrf=csrf_of(manager))
    assert resp.status_code == 200 and resp.get_json() == {'ok': True, 'removed': 0}
    assert {r['endpoint'] for r in rows(conn)} == {ENDPOINT, 'https://push.example.com/mine'}
    resp = send(owner, '/push/unsubscribe', {'endpoint': ENDPOINT}, csrf=csrf_of(owner))
    assert resp.get_json() == {'ok': True, 'removed': 1}
    assert [r['endpoint'] for r in rows(conn)] == ['https://push.example.com/mine']


@pytest.mark.parametrize('endpoint', ['http://push.example.com/send/abc', 'ftp://x', 'https://', '', None,
                                      'javascript:alert(1)', 'https://push.example.com/a b',
                                      'https://push.example.com/' + 'x' * 1000])
def test_a_non_https_or_bad_endpoint_gets_400(world, conn, keys, endpoint):
    c = world['clients']['owner']
    resp = send(c, '/push/subscribe', sub(endpoint), csrf=csrf_of(c))
    assert resp.status_code == 400 and resp.get_json()['ok'] is False
    assert send(c, '/push/unsubscribe', {'endpoint': endpoint}, csrf=csrf_of(c)).status_code == 400
    assert rows(conn) == []


@pytest.mark.parametrize('body', [sub(p256dh=''), sub(auth=None), sub(auth='bad key!'), {'endpoint': ENDPOINT},
                                  {'endpoint': ENDPOINT, 'keys': 'x'}, [ENDPOINT], 'text'])
def test_missing_or_bad_keys_get_400(world, conn, keys, body):
    c = world['clients']['owner']
    assert send(c, '/push/subscribe', body, csrf=csrf_of(c)).status_code == 400
    assert rows(conn) == []


def test_a_body_that_is_not_json_gets_400(world, conn, keys):
    c = world['clients']['owner']
    assert send(c, '/push/subscribe', None, csrf=csrf_of(c), raw='not json').status_code == 400


def test_signed_out_gets_refused(app, keys, conn):
    c = new_client(app)
    resp = send(c, '/push/subscribe', sub(), csrf=token(c, '/signin'))   # a valid pre-sign-in token
    assert resp.status_code == 401
    assert rows(conn) == []


def test_subscribe_without_keys_is_refused(world, conn):
    c = world['clients']['owner']
    resp = send(c, '/push/subscribe', sub(), csrf=csrf_of(c))
    assert resp.status_code == 409 and 'not set up' in resp.get_json()['error']
    assert rows(conn) == []


def push_section(page):
    return re.search(r'<section class="form-card" id="push">.*?</section>', page, re.S).group(0)


def test_the_account_page_without_keys_says_push_is_not_set_up(world):
    section = push_section(world['clients']['owner'].get('/account').get_data(as_text=True))
    assert 'Notifications on this browser' in section
    assert 'push is not set up on this service' in section.lower()
    assert 'Turn on' not in section and 'data-vapid-public-key' not in section


def test_the_account_page_with_keys_offers_turn_on_and_off(world, keys):
    c = world['clients']['owner']
    section = push_section(c.get('/account').get_data(as_text=True))
    assert 'Turn on' in section and 'Turn off' in section and 'not set up' not in section
    assert 'data-vapid-public-key="%s"' % KEYS['CENTRAL_VAPID_PUBLIC_KEY'] in section
    assert KEYS['CENTRAL_VAPID_PRIVATE_KEY'] not in c.get('/account').get_data(as_text=True)
    assert 'data-csrf-token="%s"' % csrf_of(c) in section
    assert 'data-subscribe-url="/push/subscribe"' in section and 'data-unsubscribe-url="/push/unsubscribe"' in section
    assert 'Not turned on in any browser yet.' in section
    send(c, '/push/subscribe', sub(), csrf=csrf_of(c))
    assert 'Turned on in 1 browser for your account.' in push_section(c.get('/account').get_data(as_text=True))
    assert 'style=' not in section and '<script' not in section


def test_keys_are_read_from_the_environment_and_all_three_are_needed():
    env = dict(KEYS)
    cfg = config.load({'TESTING': True}, environ=env)
    assert config.push_configured(cfg) and cfg['CENTRAL_VAPID_SUBJECT'] == 'mailto:ops@example.com'
    for name in KEYS:
        partial = dict(KEYS, **{name: ''})
        assert not config.push_configured(config.load({'TESTING': True}, environ=partial))


def test_init_db_adds_the_push_subscription_table_to_an_existing_database(tmp_path):
    db_url = 'sqlite:///%s' % (tmp_path / 'old.db')
    old = db.connect(db_url)
    old.execute(OLD_PROJECT)
    old.execute("INSERT INTO project (id, slug, name, created_at) VALUES ('p1', 'old', 'Old', ?)", (AT,))
    old.commit()
    old.close()
    db.init_db(db_url)
    conn = db.connect(db_url)
    try:
        assert {'id', 'account_id', 'endpoint', 'p256dh', 'auth', 'user_agent', 'created_at', 'last_ok_at',
                'failures'} == conn.columns('push_subscription')
        assert conn.query_one("SELECT slug FROM project WHERE id = 'p1'")['slug'] == 'old'
    finally:
        conn.close()
