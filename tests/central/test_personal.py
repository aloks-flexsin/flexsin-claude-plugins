"""Central service: sign-in from Claude (device codes) and personal tokens (decision record 0008, second part)."""
import datetime
import json

import pytest

from central import accounts, audit, personal, projects, tokens

from .test_access import PW, new_client, post, signed_in
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)

EVENT = {'schema': 'event.v1', 'id': 'e1', 'time': '2026-11-23T09:00:00+00:00', 'run': 'r1', 'project': 'alpha',
         'type': 'run_start', 'item': None, 'detail': {'user': 'alok', 'account': 'bhawish'},
         'evidence_class': 'runner_observed'}


@pytest.fixture
def clock(app):
    class Clock:
        now = datetime.datetime(2026, 11, 23, 9, 0, tzinfo=datetime.timezone.utc)

        def __call__(self):
            return self.now
    c = Clock()
    app.config['CLOCK'] = c
    return c


def api(app, method, path, token=None, body=None):
    c = new_client(app)
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    return c.open(path, method=method, headers=headers, data=json.dumps(body) if body is not None else None)


def start(app):
    resp = api(app, 'POST', '/api/v1/device/start', body={'client_name': 'ALOK-LAPTOP', 'repo': 'github.com/example/alpha'})
    assert resp.status_code == 200
    return resp.get_json()


def poll(app, clock, device_code):
    clock.now += datetime.timedelta(seconds=personal.INTERVAL)
    return api(app, 'POST', '/api/v1/device/token', body={'device_code': device_code})


def approve(client, user_code, decision='approve'):
    post(client, '/device', user_code=user_code)
    return post(client, '/device/decide', user_code=user_code, decision=decision)


@pytest.fixture
def signed_in_token(app, world, clock):
    """A personal token for the alpha project's manager, got through the whole device flow."""
    s = start(app)
    assert approve(world['clients']['manager'], s['user_code']).status_code == 200
    resp = poll(app, clock, s['device_code'])
    assert resp.status_code == 200
    return resp.get_json()['token']


# ---------- the device flow ----------

def test_start_gives_codes_and_the_address(app, clock):
    s = start(app)
    assert len(s['device_code']) > 30 and len(s['user_code']) == 9 and s['user_code'][4] == '-'
    assert s['verification_uri'].endswith('/device') and s['interval'] == 5 and s['expires_in'] == 600


def test_pending_then_approved_then_used(app, world, conn, clock):
    s = start(app)
    assert poll(app, clock, s['device_code']).get_json()['error'] == 'authorization_pending'
    page = post(world['clients']['manager'], '/device', user_code=s['user_code'].lower().replace('-', ' '))
    assert 'ALOK-LAPTOP' in page.get_data(as_text=True) and 'Approve only a code you started yourself' in page.get_data(as_text=True)
    assert post(world['clients']['manager'], '/device/decide', user_code=s['user_code'], decision='approve').status_code == 200
    resp = poll(app, clock, s['device_code'])
    body = resp.get_json()
    assert resp.status_code == 200 and body['token'].startswith('arp_') and body['email'] == 'manager@example.com'
    assert poll(app, clock, s['device_code']).get_json()['error'] == 'expired_token'      # delivered once
    [row] = conn.query('SELECT * FROM personal_token')
    assert body['token'] not in str(dict(row)) and row['name'] == 'ALOK-LAPTOP'
    assert len(audit.rows(conn, 'device.approve')) == 1 and len(audit.rows(conn, 'personal_token.create')) == 1
    assert body['token'] not in str(audit.rows(conn))


def test_denied(app, world, clock):
    s = start(app)
    approve(world['clients']['manager'], s['user_code'], 'deny')
    assert poll(app, clock, s['device_code']).get_json()['error'] == 'access_denied'


def test_expired_after_ten_minutes(app, world, clock):
    s = start(app)
    clock.now += personal.DEVICE_LIFETIME
    assert approve(world['clients']['manager'], s['user_code']).status_code == 400
    assert poll(app, clock, s['device_code']).get_json()['error'] == 'expired_token'


def test_polling_too_fast_is_slowed_down(app, clock):
    s = start(app)
    api(app, 'POST', '/api/v1/device/token', body={'device_code': s['device_code']})
    resp = api(app, 'POST', '/api/v1/device/token', body={'device_code': s['device_code']})
    assert resp.get_json()['error'] == 'slow_down'


def test_a_code_cannot_be_decided_twice(app, world, clock):
    s = start(app)
    approve(world['clients']['manager'], s['user_code'])
    assert approve(world['clients']['viewer'], s['user_code'], 'deny').status_code == 400


def test_approving_needs_a_signed_in_person(app, clock):
    s = start(app)
    assert new_client(app).get('/device').status_code == 302
    assert poll(app, clock, s['device_code']).get_json()['error'] == 'authorization_pending'


def test_unknown_codes(app, world, clock):
    assert post(world['clients']['manager'], '/device', user_code='ZZZZ-ZZZZ').status_code == 400
    assert api(app, 'POST', '/api/v1/device/token', body={'device_code': 'nope'}).get_json()['error'] == 'expired_token'
    assert api(app, 'POST', '/api/v1/device/token', body={}).get_json()['error'] == 'expired_token'


def test_starting_is_rate_limited(app, clock):
    for _ in range(personal.START_LIMIT):
        start(app)
    assert api(app, 'POST', '/api/v1/device/start', body={}).status_code == 429


# ---------- using a personal token ----------

def test_the_token_works_for_a_project_the_person_belongs_to(app, world, conn, signed_in_token):
    resp = api(app, 'GET', '/api/v1/project?project=alpha', signed_in_token)
    assert resp.status_code == 200 and resp.get_json()['account'] == 'manager@example.com'
    resp = api(app, 'POST', '/api/v1/events?project=alpha', signed_in_token, [EVENT])
    assert resp.status_code == 200
    [row] = conn.query('SELECT account_id FROM event')
    assert row['account_id'] == world['accounts']['manager']['id']             # stamped by the service
    assert api(app, 'GET', '/api/v1/deliveries/pending?project=alpha', signed_in_token).status_code == 200


def test_the_runs_page_shows_who_the_service_verified(app, world, signed_in_token):
    api(app, 'POST', '/api/v1/events?project=alpha', signed_in_token, [EVENT])
    page = world['clients']['viewer'].get('/projects/alpha/runs').get_data(as_text=True)
    assert 'signed in as manager@example.com' in page


def test_no_project_or_another_project_is_refused(app, world, conn, signed_in_token):
    projects.create(conn, 'beta', 'Beta', AT, 'https://github.com/example/beta.git')
    assert api(app, 'GET', '/api/v1/project', signed_in_token).status_code == 403
    assert api(app, 'GET', '/api/v1/project?project=beta', signed_in_token).status_code == 403
    assert api(app, 'GET', '/api/v1/project?project=nope', signed_in_token).status_code == 403


def test_viewers_cannot_run_runners(app, world, clock):
    s = start(app)
    approve(world['clients']['viewer'], s['user_code'])
    token = poll(app, clock, s['device_code']).get_json()['token']
    assert api(app, 'GET', '/api/v1/project?project=alpha', token).status_code == 403


def test_losing_membership_or_being_disabled_stops_it_at_once(app, world, conn, signed_in_token):
    manager = world['accounts']['manager']
    accounts.set_disabled(conn, manager['id'], True)
    assert api(app, 'GET', '/api/v1/project?project=alpha', signed_in_token).status_code == 401
    accounts.set_disabled(conn, manager['id'], False)
    assert api(app, 'GET', '/api/v1/project?project=alpha', signed_in_token).status_code == 200
    projects.remove_member(conn, world['alpha']['id'], manager['id'])
    assert api(app, 'GET', '/api/v1/project?project=alpha', signed_in_token).status_code == 403


def test_ninety_days_unused_stops_it(app, clock, signed_in_token):
    assert api(app, 'GET', '/api/v1/project?project=alpha', signed_in_token).status_code == 200
    clock.now += datetime.timedelta(days=89)
    assert api(app, 'GET', '/api/v1/project?project=alpha', signed_in_token).status_code == 200      # use resets it
    clock.now += personal.MAX_IDLE
    assert api(app, 'GET', '/api/v1/project?project=alpha', signed_in_token).status_code == 401


def test_revoked_on_my_tokens_and_by_an_admin(app, world, conn, clock, signed_in_token):
    manager = world['clients']['manager']
    page = manager.get('/account/tokens').get_data(as_text=True)
    assert 'ALOK-LAPTOP' in page and signed_in_token not in page
    [row] = conn.query('SELECT id FROM personal_token')
    assert post(manager, '/account/tokens/%s/revoke' % row['id']).status_code == 302
    assert api(app, 'GET', '/api/v1/project?project=alpha', signed_in_token).status_code == 401
    assert post(world['clients']['viewer'], '/account/tokens/%s/revoke' % row['id']).status_code == 404   # not theirs
    s = start(app)
    approve(manager, s['user_code'])
    second = poll(app, clock, s['device_code']).get_json()['token']
    assert post(world['clients']['admin'], '/admin/accounts/%s/tokens/revoke' % world['accounts']['manager']['id']).status_code == 302
    assert api(app, 'GET', '/api/v1/project?project=alpha', second).status_code == 401


def test_a_project_token_with_another_projects_name_is_refused(app, world, conn):
    raw, _ = tokens.create(conn, world['alpha']['id'], ['pull'], None, AT)
    assert api(app, 'GET', '/api/v1/project?project=alpha', raw).status_code == 200
    assert api(app, 'GET', '/api/v1/project?project=beta', raw).status_code == 403
    assert api(app, 'GET', '/api/v1/project', raw).status_code == 200
