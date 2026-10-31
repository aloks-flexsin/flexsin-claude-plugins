import datetime
import json
import os
import sys
import uuid

import pytest

from central import db, ingest, projects, tokens

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'auto-runner', 'bin'))
if BIN not in sys.path:
    sys.path.insert(0, BIN)

import autoevents  # noqa: E402

START = datetime.datetime(2026, 10, 31, 9, 0, tzinfo=datetime.timezone.utc)
URL = '/api/v1/events'


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


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
def world(app, conn, clock):
    alpha = projects.create(conn, 'alpha', 'Alpha', '2026-10-31T09:00:00Z')
    beta = projects.create(conn, 'beta', 'Beta', '2026-10-31T09:00:00Z')
    ingest_raw, _ = tokens.create(conn, alpha['id'], ['ingest'], None, '2026-10-31T09:00:00Z')
    pull_raw, _ = tokens.create(conn, alpha['id'], ['pull'], None, '2026-10-31T09:00:00Z')
    beta_raw, _ = tokens.create(conn, beta['id'], ['ingest'], None, '2026-10-31T09:00:00Z')
    return {'app': app, 'alpha': alpha, 'beta': beta, 'token': ingest_raw, 'pull': pull_raw, 'beta_token': beta_raw}


def send(app, raw, body=None, data=None, headers=None):
    c = app.test_client()
    c.environ_base['wsgi.url_scheme'] = 'https'
    h = {'Authorization': 'Bearer ' + raw, 'Content-Type': 'application/json'}
    h.update(headers or {})
    if data is None:
        data = json.dumps(body)
    return c.post(URL, data=data, headers=h)


def event(**over):
    e = {'schema': 'event.v1', 'id': uuid.uuid4().hex, 'time': '2026-10-31T09:00:00+00:00', 'run': '20261031-0900',
         'project': 'alpha', 'type': 'tests', 'item': 'C7', 'detail': {'result': 'pass', 'exit': 0},
         'evidence_class': 'independent_test'}
    e.update(over)
    return e


def count(conn, table):
    return conn.query_one('SELECT COUNT(*) AS n FROM %s' % table)['n']


# --- parity with the plugin ---

def test_lists_equal_the_plugins():
    assert ingest.SCHEMA == autoevents.SCHEMA
    assert ingest.FIELDS == autoevents.FIELDS
    assert ingest.TYPES == autoevents.TYPES
    assert ingest.EVIDENCE == autoevents.EVIDENCE


def test_events_written_by_the_real_ledger_are_accepted_unchanged(world, conn, tmp_path):
    ledger = autoevents.Ledger(tmp_path / '.auto', '20261031-0900', 'alpha')
    ledger.emit('run_start')
    ledger.emit('item_state', 'C7', {'from': 'open', 'to': 'running'})
    ledger.emit('tests', 'C7', {'result': 'pass', 'exit': 0}, 'independent_test')
    ledger.emit('blocker', 'C7', {'action': 'raised', 'id': 'B-1a2b3c4d5e6f'}, 'agent_reported')
    ledger.emit('browser', 'C7', {'result': 'pass', 'evidence': 'E1', 'failed': [], 'counts': {}, 'errors': []})
    ledger.emit('run_end', None, {'exit': 0, 'reason': 'done'})
    lines = (tmp_path / '.auto' / 'events.jsonl').read_text(encoding='utf-8').splitlines()
    one = send(world['app'], world['token'], data=lines[0])
    assert one.status_code == 200, one.get_json()
    rest = send(world['app'], world['token'], data='[' + ','.join(lines[1:]) + ']')
    assert rest.status_code == 200, rest.get_json()
    assert rest.get_json()['accepted'] == 5
    stored = {r['id']: json.loads(r['body']) for r in conn.query('SELECT * FROM event')}
    for written in autoevents.read(tmp_path / '.auto'):
        assert stored[written['id']] == written
    # Every event the plugin's own validator accepts, the service accepts, and the reverse for these samples.
    for written in autoevents.read(tmp_path / '.auto'):
        assert ingest.validate(written) == [] == autoevents.validate(written)


@pytest.mark.parametrize('broken', [
    {'type': 'nonsense'}, {'evidence_class': 'rumour'}, {'detail': []}, {'item': None},
    {'type': 'run_start'}, {'time': 'yesterday'}, {'id': ''}, {'extra': 1},
])
def test_the_service_and_the_plugin_refuse_the_same_events(broken):
    e = event(**broken)
    assert ingest.validate(e) and autoevents.validate(e)


# --- the request ---

def test_one_object_or_a_list(world, conn):
    assert send(world['app'], world['token'], event()).get_json()['accepted'] == 1
    resp = send(world['app'], world['token'], [event(), event()])
    assert resp.status_code == 200 and resp.get_json()['accepted'] == 2
    assert count(conn, 'event') == 3
    row = conn.query_one('SELECT * FROM event LIMIT 1')
    assert row['project_id'] == world['alpha']['id'] and row['received_at'] == '2026-10-31T09:00:00Z'


def test_needs_an_ingest_token(world):
    assert send(world['app'], 'art_unknown', event()).status_code == 401
    assert send(world['app'], world['pull'], event()).status_code == 403


def test_duplicate_is_counted_and_changes_nothing(world, conn):
    e = event()
    assert send(world['app'], world['token'], e).get_json()['accepted'] == 1
    again = send(world['app'], world['token'], [e, event()])
    assert again.status_code == 200
    assert again.get_json()['accepted'] == 1 and again.get_json()['duplicates'] == 1
    assert again.get_json()['duplicate_ids'] == [e['id']]
    assert count(conn, 'event') == 2


def test_same_id_different_body_is_409_and_stores_nothing(world, conn):
    e = event()
    send(world['app'], world['token'], e)
    changed = dict(e, detail={'result': 'fail', 'exit': 1})
    resp = send(world['app'], world['token'], [event(), changed])
    assert resp.status_code == 409
    assert resp.get_json()['id'] == e['id']
    assert count(conn, 'event') == 1
    assert json.loads(conn.query_one('SELECT body FROM event')['body'])['detail'] == {'result': 'pass', 'exit': 0}


def test_same_id_twice_in_one_request(world, conn):
    e = event()
    assert send(world['app'], world['token'], [e, e]).get_json() == {
        'accepted': 1, 'duplicates': 1, 'ids': [e['id']], 'duplicate_ids': [e['id']]}
    assert send(world['app'], world['token'], [e, dict(e, run='other')]).status_code == 409


def test_an_id_stored_by_another_project_is_409(world, conn):
    e = event(project='beta')
    assert send(world['app'], world['beta_token'], e).status_code == 200
    assert send(world['app'], world['token'], dict(e, project='alpha')).status_code == 409


@pytest.mark.parametrize('schema', ['event.v2', 'event.v0', 'nonsense', None])
def test_unknown_schema_version_is_400(world, conn, schema):
    resp = send(world['app'], world['token'], [event(), event(schema=schema)])
    assert resp.status_code == 400
    assert 'schema' in resp.get_json()['error'] and resp.get_json()['index'] == 1
    assert count(conn, 'event') == 0


@pytest.mark.parametrize('data', ['not json', '[]', '"a string"', '[1]', '{}'])
def test_bad_bodies_are_400(world, conn, data):
    assert send(world['app'], world['token'], data=data).status_code == 400
    assert count(conn, 'event') == 0


def test_invalid_event_is_400_with_the_reasons(world, conn):
    resp = send(world['app'], world['token'], [event(), event(type='nonsense')])
    assert resp.status_code == 400
    assert resp.get_json()['errors'] and resp.get_json()['index'] == 1
    assert count(conn, 'event') == 0


def test_body_over_256_kb_is_413(world, conn):
    big = event(detail={'log': 'x' * (256 * 1024)})
    assert send(world['app'], world['token'], big).status_code == 413
    at_limit = event(detail={'log': ''})
    pad = 256 * 1024 - len(json.dumps(at_limit))
    at_limit['detail']['log'] = 'x' * pad
    data = json.dumps(at_limit)
    assert len(data) == 256 * 1024
    assert send(world['app'], world['token'], data=data).status_code == 200
    assert count(conn, 'event') == 1


def test_wrong_project_is_refused(world, conn):
    resp = send(world['app'], world['token'], [event(), event(project='beta')])
    assert resp.status_code == 403
    assert count(conn, 'event') == 0
    assert send(world['app'], world['beta_token'], event()).status_code == 403


def test_rate_limit_per_token_with_the_clock(world, conn, clock):
    app = world['app']
    for _ in range(120):
        assert send(app, world['token'], data='[]').status_code == 400  # counted even when refused
    blocked = send(app, world['token'], event())
    assert blocked.status_code == 429
    assert int(blocked.headers['Retry-After']) >= 1
    # Another token is not affected.
    assert send(app, world['beta_token'], event(project='beta')).status_code == 200
    clock.now = START + datetime.timedelta(seconds=59)
    assert send(app, world['token'], event()).status_code == 429
    clock.now = START + datetime.timedelta(seconds=60)
    assert send(app, world['token'], event()).status_code == 200
    assert count(conn, 'event') == 2


def test_rate_limiter_window():
    limiter = ingest.RateLimiter(limit=2, window=datetime.timedelta(seconds=10))
    assert limiter.allow('t', START) == (True, 0)
    assert limiter.allow('t', START + datetime.timedelta(seconds=4))[0]
    assert limiter.allow('t', START + datetime.timedelta(seconds=5)) == (False, 5)
    assert limiter.allow('t', START + datetime.timedelta(seconds=10))[0]


# --- redaction ---

SECRETS = [
    'art_' + 'A' * 43,
    'Bearer abcdefghijklmnop1234',
    'sk-ant-api03-' + 'b' * 30,
    'ghp_' + 'c' * 36,
    'AKIAIOSFODNN7EXAMPLE',
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U',
    '-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----',
]


def test_secrets_in_detail_strings_are_redacted_before_storing(world, conn):
    e = event(type='findings', detail={
        'errors': ['login failed with %s' % s for s in SECRETS],
        'nested': {'url': 'https://admin:hunter2-long@db.example.com/x', 'note': 'password=hunter2-long ok'},
        'password': 'plain-but-named', 'api_key': 12345, 'count': 3, 'result': 'fail'})
    assert send(world['app'], world['token'], e).status_code == 200
    stored = conn.query_one('SELECT body FROM event')['body']
    for s in SECRETS + ['hunter2-long', 'plain-but-named', '12345']:
        assert s not in stored
    detail = json.loads(stored)['detail']
    assert detail['errors'][0] == 'login failed with [redacted]'
    assert detail['nested']['url'] == 'https://[redacted]@db.example.com/x'
    assert detail['password'] == '[redacted]' and detail['api_key'] == '[redacted]'
    assert detail['count'] == 3 and detail['result'] == 'fail'
    # The same body sent again is still a duplicate (comparison is on the redacted body).
    assert send(world['app'], world['token'], e).get_json()['duplicates'] == 1


def test_ordinary_text_is_not_redacted():
    detail = {'reason': 'tests passed', 'map': 'docs/doc-map.json', 'id': 'B-1a2b3c4d5e6f',
              'failures': ['README.md is stale'], 'exit': 0, 'ok': True}
    assert ingest.redact(detail) == detail


# --- blockers ---

def test_raised_blocker_creates_then_updates_the_row(world, conn, clock):
    first = event(type='blocker', item='C7', detail={'action': 'raised', 'id': 'B-1a2b3c4d5e6f'},
                  evidence_class='agent_reported')
    assert send(world['app'], world['token'], first).status_code == 200
    row = conn.query_one('SELECT * FROM blocker')
    task = conn.query_one('SELECT * FROM task')
    assert row['project_id'] == world['alpha']['id'] and row['blocker_id'] == 'B-1a2b3c4d5e6f'
    assert row['task_id'] == task['id'] and task['item'] == 'C7' and task['project_id'] == world['alpha']['id']
    assert row['status'] == 'OPEN' and json.loads(row['body'])['id'] == first['id']

    clock.now = START + datetime.timedelta(minutes=5)
    second = event(type='blocker', item='C8', detail={'action': 'raised', 'id': 'B-1a2b3c4d5e6f'},
                   evidence_class='agent_reported')
    assert send(world['app'], world['token'], second).status_code == 200
    assert count(conn, 'blocker') == 1
    row = conn.query_one('SELECT * FROM blocker')
    assert row['updated_at'] == '2026-10-31T09:05:00Z' and row['created_at'] == '2026-10-31T09:00:00Z'
    assert row['task_id'] == conn.query_one("SELECT id FROM task WHERE item = 'C8'")['id']
    assert json.loads(row['body'])['id'] == second['id']


def test_other_blocker_actions_and_duplicates_do_not_touch_blockers(world, conn):
    send(world['app'], world['token'], [
        event(type='blocker', detail={'action': 'rejected', 'file': 'x.json', 'reasons': ['bad']}),
        event(type='blocker', detail={'action': 'resumed', 'id': 'B-1'})])
    assert count(conn, 'blocker') == 0
    raised = event(type='blocker', detail={'action': 'raised', 'id': 'B-2'})
    send(world['app'], world['token'], raised)
    send(world['app'], world['token'], raised)
    assert count(conn, 'blocker') == 1 and count(conn, 'task') == 1


def test_same_blocker_id_in_two_projects_is_two_rows(world, conn):
    send(world['app'], world['token'], event(type='blocker', detail={'action': 'raised', 'id': 'B-9'}))
    send(world['app'], world['beta_token'], event(project='beta', type='blocker', detail={'action': 'raised', 'id': 'B-9'}))
    assert count(conn, 'blocker') == 2


def test_raised_blocker_without_a_valid_id_is_400(world, conn):
    for bad in ({'action': 'raised'}, {'action': 'raised', 'id': '../etc'}, {'action': 'raised', 'id': 'x' * 65}):
        assert send(world['app'], world['token'], event(type='blocker', detail=bad)).status_code == 400
    assert count(conn, 'event') == 0 and count(conn, 'blocker') == 0
