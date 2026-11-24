import datetime
import json
import os
import sys
import uuid

import pytest

from central import accounts, db, identities, personal, projects, tokens

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'auto-runner', 'bin'))
if BIN not in sys.path:
    sys.path.insert(0, BIN)

import autosafe  # noqa: E402

NOW = '2026-10-31T09:00:00Z'
LATER = '2026-10-31T10:00:00Z'
PASSWORD = 'correct horse battery staple 42'


@pytest.fixture
def conn(app, db_url):
    c = db.connect(db_url)
    yield c
    c.close()


@pytest.fixture
def world(app, conn):
    alpha = projects.create(conn, 'alpha', 'Alpha', NOW, 'https://github.com/example/repo.git')
    raw, _ = tokens.create(conn, alpha['id'], ['ingest'], None, NOW)
    return {'app': app, 'alpha': alpha, 'token': raw}


def count(conn, table):
    return conn.query_one('SELECT COUNT(*) AS n FROM %s' % table)['n']


def person(conn, email):
    return accounts.create(conn, email, PASSWORD, now=NOW)


def start(user, account, **over):
    e = {'schema': 'event.v1', 'id': uuid.uuid4().hex, 'time': '2026-10-31T09:00:00+00:00', 'run': '20261031-0900',
         'project': 'alpha', 'type': 'run_start', 'item': None, 'detail': {'user': user, 'account': account},
         'evidence_class': 'runner_observed'}
    e.update(over)
    return e


def post(app, raw, body, query=''):
    c = app.test_client()
    c.environ_base['wsgi.url_scheme'] = 'https'
    return c.post('/api/v1/events' + query, data=json.dumps(body),
                  headers={'Authorization': 'Bearer ' + raw, 'Content-Type': 'application/json'})


def developer_token(conn, world, email='alok@example.com'):
    acct = person(conn, email)
    projects.add_member(conn, world['alpha']['id'], email, 'developer', NOW)
    moment = datetime.datetime(2026, 10, 31, 9, 0, tzinfo=datetime.timezone.utc)
    return acct, personal.create_token(conn, acct['id'], 'laptop', moment)


def pairs(conn, account_id):
    return [(r['username'], r['claude_account']) for r in identities.of_account(conn, account_id)]


def test_name_pattern_equals_the_runners():
    assert identities.NAME.pattern == autosafe.RUNNER_NAME.pattern


def test_one_person_two_claude_accounts_gives_two_rows(conn):
    alok = person(conn, 'alok@example.com')
    identities.record(conn, alok['id'], 'alok', 'bhawish', NOW)
    identities.record(conn, alok['id'], 'alok', 'alok-pers', NOW)
    assert pairs(conn, alok['id']) == [('alok', 'alok-pers'), ('alok', 'bhawish')]


def test_two_people_one_claude_account_gives_two_rows(conn):
    alok, priya = person(conn, 'alok@example.com'), person(conn, 'priya@example.com')
    identities.record(conn, alok['id'], 'alok', 'bhawish', NOW)
    identities.record(conn, priya['id'], 'priya', 'bhawish', NOW)
    assert count(conn, 'claude_identity') == 2
    assert pairs(conn, priya['id']) == [('priya', 'bhawish')]


def test_surfaces_make_separate_rows_and_unknown_ones_record_nothing(conn):
    alok = person(conn, 'alok@example.com')
    a = identities.record(conn, alok['id'], 'alok', 'bhawish', NOW)
    b = identities.record(conn, alok['id'], 'alok', 'bhawish', NOW, 'cli')
    assert a and b and a != b
    assert [r['surface'] for r in identities.of_account(conn, alok['id'])] == ['auto-runner', 'cli']
    for bad in ('phone', '', None, 'CLI'):
        assert identities.record(conn, alok['id'], 'alok', 'bhawish', NOW, bad) is None
    assert count(conn, 'claude_identity') == 2


def test_a_repeat_updates_last_seen_only_and_audits_once(conn):
    alok = person(conn, 'alok@example.com')
    first = identities.record(conn, alok['id'], 'alok', 'bhawish', NOW)
    second = identities.record(conn, alok['id'], 'alok', 'bhawish', LATER)
    conn.commit()
    row = identities.of_account(conn, alok['id'])[0]
    assert first == second and count(conn, 'claude_identity') == 1
    assert (row['first_seen'], row['last_seen']) == (NOW, LATER)
    assert conn.query_one("SELECT COUNT(*) AS n FROM audit WHERE action = 'identity_seen'")['n'] == 1


@pytest.mark.parametrize('user, account', [
    ('', 'bhawish'), ('alok', ''), (None, 'bhawish'), ('alok', None), ('Alok', 'bhawish'), ('a b', 'x'),
    ('alok', 'a..b'), ('alok', 'x.lock'), (5, 'x'), ('alok', 'x' * 65)])
def test_invalid_values_record_nothing(conn, user, account):
    alok = person(conn, 'alok@example.com')
    assert identities.record(conn, alok['id'], user, account, NOW) is None
    assert count(conn, 'claude_identity') == 0
    assert conn.query_one("SELECT COUNT(*) AS n FROM audit WHERE action = 'identity_seen'")['n'] == 0


def test_of_project_lists_members_only(conn, world):
    alok, stranger = person(conn, 'alok@example.com'), person(conn, 'x@example.com')
    projects.add_member(conn, world['alpha']['id'], 'alok@example.com', 'developer', NOW)
    identities.record(conn, alok['id'], 'alok', 'bhawish', NOW)
    identities.record(conn, stranger['id'], 'x', 'other', NOW)
    assert [r['claude_account'] for r in identities.of_project(conn, world['alpha']['id'])] == ['bhawish']


def test_run_start_with_a_personal_token_records_the_identity(conn, world):
    alok, raw = developer_token(conn, world)
    r = post(world['app'], raw, [start('alok', 'bhawish'), start('alok', 'alok-pers')], '?project=alpha')
    assert r.status_code == 200, r.get_json()
    assert pairs(conn, alok['id']) == [('alok', 'alok-pers'), ('alok', 'bhawish')]


def test_a_project_token_event_records_nothing(conn, world):
    assert post(world['app'], world['token'], start('alok', 'bhawish')).status_code == 200
    assert count(conn, 'claude_identity') == 0


def test_other_event_types_and_bad_names_record_nothing_from_a_personal_token(conn, world):
    _, raw = developer_token(conn, world)
    other = start('alok', 'bhawish', type='tests', item='C7', evidence_class='independent_test',
                  detail={'user': 'alok', 'account': 'bhawish', 'result': 'pass', 'exit': 0})
    r = post(world['app'], raw, [other, start('BAD NAME', 'x')], '?project=alpha')
    assert r.status_code == 200, r.get_json()
    assert count(conn, 'claude_identity') == 0
