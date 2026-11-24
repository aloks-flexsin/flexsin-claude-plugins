"""Central service: Claude accounts on the Account page and on Runs (slice 1). Events are posted through
`POST /api/v1/events` with a personal token, then both pages are read as the signed-in person."""
import datetime
import json

from central import accounts, personal, projects, runs

from .test_access import PW, signed_in
from .test_ingest import clock, conn, world  # noqa: F401  (fixtures)
from .test_runs import ev

AT = '2026-10-31T09:00:00Z'
NOW = datetime.datetime(2026, 11, 23, 12, 0, tzinfo=datetime.timezone.utc)


def _post(app, raw, events, query=''):
    c = app.test_client()
    c.environ_base['wsgi.url_scheme'] = 'https'
    return c.post('/api/v1/events' + query, data=json.dumps(events),
                  headers={'Authorization': 'Bearer ' + raw, 'Content-Type': 'application/json'})


def _alok(world, conn):
    acct = accounts.create(conn, 'alok@example.com', PW)
    accounts.set_name(conn, acct['id'], 'Alok Sharma')
    conn.commit()
    projects.add_member(conn, world['alpha']['id'], 'alok@example.com', 'developer', AT)
    return personal.create_token(conn, acct['id'], 'laptop', datetime.datetime(2026, 11, 23, 9, 0, tzinfo=datetime.timezone.utc))


def test_account_page_says_none_reported_yet(world, conn):
    _alok(world, conn)
    page = signed_in(world['app'], 'alok@example.com').get('/account').get_data(as_text=True)
    assert 'Your Claude accounts' in page
    assert 'None reported yet: your runners report it when they start' in page


def test_account_and_runs_show_the_reported_pairs(world, conn, clock):
    clock.now = NOW
    raw = _alok(world, conn)
    sent = [ev('r1', 'run_start', '2026-11-23T10:00:00+00:00', user='alok', account='bhawish', branch='b1'),
            ev('r1', 'run_end', '2026-11-23T10:30:00+00:00', exit=0, reason='queue_empty'),
            ev('r2', 'run_start', '2026-11-23T11:00:00+00:00', user='alok', account='alok-pers', branch='b2'),
            ev('r2', 'run_end', '2026-11-23T11:30:00+00:00', exit=0, reason='queue_empty')]
    assert _post(world['app'], raw, sent, '?project=alpha').status_code == 200
    client = signed_in(world['app'], 'alok@example.com')

    account = client.get('/account').get_data(as_text=True)
    assert 'None reported yet' not in account
    for pair in (('alok', 'bhawish'), ('alok', 'alok-pers')):
        assert '<td>%s</td><td>%s</td>' % pair in account

    page = client.get('/projects/alpha/runs').get_data(as_text=True)
    assert 'Alok Sharma' in page
    assert 'alok · Claude account bhawish' in page
    assert 'alok · Claude account alok-pers' in page


def test_a_project_token_run_keeps_the_old_who_text(world, conn, clock):
    clock.now = NOW
    sent = [ev('r1', 'run_start', '2026-11-23T10:00:00+00:00', user='ravi', account='bhawish', branch='b1'),
            ev('r1', 'run_end', '2026-11-23T10:30:00+00:00', exit=0, reason='queue_empty')]
    assert _post(world['app'], world['token'], sent).status_code == 200
    _alok(world, conn)
    page = signed_in(world['app'], 'alok@example.com').get('/projects/alpha/runs').get_data(as_text=True)
    assert 'bhawish account' in page and 'Claude account' not in page


def test_summarise_keeps_user_account_and_the_signed_in_name_apart():
    events = [dict(ev('r1', 'run_start', '2026-11-23T10:00:00+00:00', user='alok', account='bhawish'),
                   _signed_in='alok@example.com', _signed_in_name='Alok Sharma')]
    [r] = runs.summarise(events, NOW)
    assert (r['user'], r['account'], r['signed_in'], r['signed_in_name']) == (
        'alok', 'bhawish', 'alok@example.com', 'Alok Sharma')
    assert r['who'] == 'alok via bhawish-claude-auto-runner'
