import datetime
import json
import os
import secrets
import sys
import uuid

import pytest

from central import audit, db, deliveries, projects, tokens

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'auto-runner', 'bin'))
if BIN not in sys.path:
    sys.path.insert(0, BIN)

import autoblock  # noqa: E402
import autodeliver  # noqa: E402

START = datetime.datetime(2026, 10, 31, 9, 0, tzinfo=datetime.timezone.utc)
AT = '2026-10-31T09:00:00Z'
PENDING_URL = '/api/v1/deliveries/pending'


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


@pytest.fixture
def conn(app, db_url):
    c = db.connect(db_url)
    yield c
    c.close()


def make_account(conn, email, role='user', disabled=0):
    # Straight into the table: these tests are about decisions, not password hashing.
    account_id = secrets.token_hex(16)
    conn.execute('INSERT INTO account (id, email, password_hash, role, disabled, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                 (account_id, email, 'x', role, disabled, AT))
    conn.commit()
    return conn.query_one('SELECT * FROM account WHERE id = ?', (account_id,))


def client(app):
    c = app.test_client()
    c.environ_base['wsgi.url_scheme'] = 'https'
    return c


def raise_blocker(app, raw, project, item, blocker_id):
    e = {'schema': 'event.v1', 'id': uuid.uuid4().hex, 'time': '2026-10-31T09:00:00+00:00', 'run': '20261031-0900',
         'project': project, 'type': 'blocker', 'item': item, 'detail': {'action': 'raised', 'id': blocker_id},
         'evidence_class': 'agent_reported'}
    r = client(app).post('/api/v1/events', data=json.dumps(e),
                         headers={'Authorization': 'Bearer ' + raw, 'Content-Type': 'application/json'})
    assert r.status_code == 200, r.get_json()


@pytest.fixture
def world(app, conn):
    app.config['CLOCK'] = Clock()
    alpha = projects.create(conn, 'alpha', 'Alpha', AT)
    beta = projects.create(conn, 'beta', 'Beta', AT)
    people = {name: make_account(conn, name + '@example.com') for name in
              ('owner', 'manager', 'developer', 'viewer', 'stranger')}
    people['admin'] = make_account(conn, 'admin@example.com', role='admin')
    for role in ('owner', 'manager', 'developer', 'viewer'):
        conn.execute('INSERT INTO member (project_id, account_id, role, created_at) VALUES (?, ?, ?, ?)',
                     (alpha['id'], people[role]['id'], role, AT))
    conn.commit()
    ingest_a, _ = tokens.create(conn, alpha['id'], ['ingest'], None, AT)
    ingest_b, _ = tokens.create(conn, beta['id'], ['ingest'], None, AT)
    pull_a, _ = tokens.create(conn, alpha['id'], ['pull'], None, AT)
    pull_b, _ = tokens.create(conn, beta['id'], ['pull'], None, AT)
    raise_blocker(app, ingest_a, 'alpha', 'A1', 'B-alpha1')
    raise_blocker(app, ingest_a, 'alpha', 'A2', 'B-alpha2')
    raise_blocker(app, ingest_b, 'beta', 'X1', 'B-beta1')
    return {'app': app, 'alpha': alpha, 'beta': beta, 'people': people, 'pull': pull_a, 'pull_b': pull_b,
            'ingest': ingest_a}


def decide(conn, w, blocker_id='B-alpha1', who='owner', project='alpha', option='Orange', text='because', context=''):
    return deliveries.record_decision(conn, w[project], blocker_id, w['people'][who], option, text, context, now=START)


def pull(w, raw=None):
    r = client(w['app']).get(PENDING_URL, headers={'Authorization': 'Bearer ' + (raw or w['pull'])})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['deliveries']


def ack(w, did, raw=None):
    return client(w['app']).post('/api/v1/deliveries/%s/ack' % did,
                                 headers={'Authorization': 'Bearer ' + (raw or w['pull'])})


def row(conn, did):
    return conn.query_one('SELECT * FROM delivery WHERE delivery_id = ?', (did,))


# --- recording a decision ---

def test_decision_fields_equal_the_plugins():
    assert deliveries.DECISION_SCHEMA == autoblock.DECISION_SCHEMA
    assert deliveries.DECISION_FIELDS == autoblock.DECISION_FIELDS


def test_record_decision_writes_decision_v1_and_a_pending_delivery(world, conn):
    out = decide(conn, world)
    assert out == {'delivery_id': 'B-alpha1.v1', 'project': 'alpha', 'task': 'A1', 'blocker_id': 'B-alpha1',
                   'version': 1, 'state': 'pending', 'attempts': 0, 'last_error': None}
    stored = json.loads(conn.query_one('SELECT body FROM decision')['body'])
    expected = autoblock.new_decision('B-alpha1', 1, 'owner@example.com', 'owner', 'Orange', 'because', '')
    expected['created_at'] = '2026-10-31T09:00:00+00:00'
    assert stored == expected
    assert autoblock.validate_decision(stored) == []
    assert autodeliver.delivery_id('B-alpha1', 1) == out['delivery_id']
    assert conn.query_one("SELECT status FROM blocker WHERE blocker_id = 'B-alpha1'")['status'] == 'PENDING_DELIVERY'


def test_each_decision_takes_the_next_version(world, conn):
    assert decide(conn, world)['delivery_id'] == 'B-alpha1.v1'
    assert decide(conn, world, who='manager', option=None, text='changed my mind')['delivery_id'] == 'B-alpha1.v2'
    assert decide(conn, world, blocker_id='B-alpha2', who='admin')['delivery_id'] == 'B-alpha2.v1'
    v2 = json.loads(conn.query_one('SELECT body FROM decision WHERE version = 2')['body'])
    assert v2['answer'] == {'text': 'changed my mind'} and v2['role'] == 'manager'


def test_an_audit_row_per_decision(world, conn):
    decide(conn, world)
    decide(conn, world, who='admin')
    rows = audit.rows(conn, 'decision.record')
    assert [r['detail']['delivery_id'] for r in rows] == ['B-alpha1.v1', 'B-alpha1.v2']
    assert rows[0]['account_id'] == world['people']['owner']['id']
    assert rows[1]['account_id'] == world['people']['admin']['id']
    assert all(r['project_id'] == world['alpha']['id'] for r in rows)


@pytest.mark.parametrize('who, allowed', [('owner', True), ('manager', True), ('admin', True),
                                          ('developer', False), ('viewer', False), ('stranger', False)])
def test_only_owners_managers_and_admins_decide(world, conn, who, allowed):
    if allowed:
        assert decide(conn, world, who=who)['state'] == 'pending'
    else:
        with pytest.raises(deliveries.DecisionRefused):
            decide(conn, world, who=who)
        assert conn.query_one('SELECT COUNT(*) AS n FROM delivery')['n'] == 0
        assert audit.rows(conn, 'decision.record.refused')[0]['account_id'] == world['people'][who]['id']


def test_a_disabled_admin_cannot_decide(world, conn):
    gone = make_account(conn, 'gone@example.com', role='admin', disabled=1)
    with pytest.raises(deliveries.DecisionRefused):
        deliveries.record_decision(conn, world['alpha'], 'B-alpha1', gone, 'Orange', None, '', now=START)


def test_bad_decisions_are_refused_and_store_nothing(world, conn):
    with pytest.raises(deliveries.DecisionError):
        decide(conn, world, blocker_id='B-nope')
    with pytest.raises(deliveries.DecisionError):
        decide(conn, world, blocker_id='B-beta1')          # another project's blocker
    with pytest.raises(deliveries.DecisionError):
        decide(conn, world, option=None, text='  ')
    assert conn.query_one('SELECT COUNT(*) AS n FROM decision')['n'] == 0
    assert conn.query_one('SELECT COUNT(*) AS n FROM delivery')['n'] == 0


# --- pulling ---

def test_pull_hands_over_pending_rows_and_counts_attempts(world, conn):
    decide(conn, world)
    items = pull(world)
    assert len(items) == 1
    item = items[0]
    assert set(item) == {'delivery_id', 'blocker_id', 'version', 'project', 'task', 'decision'}
    assert (item['delivery_id'], item['blocker_id'], item['version'], item['project'], item['task']) == \
        ('B-alpha1.v1', 'B-alpha1', 1, 'alpha', 'A1')
    assert autoblock.validate_decision(item['decision']) == []
    r = row(conn, 'B-alpha1.v1')
    assert (r['status'], r['attempts']) == ('delivered', 1)
    # A delivered row is offered again until acknowledged; every hand-over counts.
    assert [i['delivery_id'] for i in pull(world)] == ['B-alpha1.v1']
    assert row(conn, 'B-alpha1.v1')['attempts'] == 2


def test_pull_orders_by_delivery_id_and_skips_acknowledged(world, conn):
    decide(conn, world, blocker_id='B-alpha2')
    decide(conn, world)
    decide(conn, world)
    assert [i['delivery_id'] for i in pull(world)] == ['B-alpha1.v1', 'B-alpha1.v2', 'B-alpha2.v1']
    assert ack(world, 'B-alpha1.v1').status_code == 200
    assert [i['delivery_id'] for i in pull(world)] == ['B-alpha1.v2', 'B-alpha2.v1']


def test_no_cross_project_rows(world, conn):
    decide(conn, world)
    decide(conn, world, blocker_id='B-beta1', who='admin', project='beta')
    assert [i['delivery_id'] for i in pull(world)] == ['B-alpha1.v1']
    assert [i['delivery_id'] for i in pull(world, world['pull_b'])] == ['B-beta1.v1']
    assert all(i['project'] == 'beta' for i in pull(world, world['pull_b']))


def test_a_reply_that_fails_to_serialise_marks_the_row_failed(world, conn):
    decide(conn, world)
    decide(conn, world, blocker_id='B-alpha2')
    conn.execute("UPDATE decision SET body = '{not json' WHERE version = 1 AND blocker_ref ="
                 " (SELECT id FROM blocker WHERE blocker_id = 'B-alpha1')")
    conn.commit()
    assert [i['delivery_id'] for i in pull(world)] == ['B-alpha2.v1']
    r = row(conn, 'B-alpha1.v1')
    assert (r['status'], r['attempts']) == ('failed', 1)
    assert r['last_error'].startswith('reply could not be built')
    # Failed rows are offered again by the next pull; once the reply builds, it is delivered and the error cleared.
    conn.execute("UPDATE decision SET body = ? WHERE version = 1 AND blocker_ref ="
                 " (SELECT id FROM blocker WHERE blocker_id = 'B-alpha1')",
                 (json.dumps(autoblock.new_decision('B-alpha1', 1, 'owner@example.com', 'owner', 'Orange')),))
    conn.commit()
    assert [i['delivery_id'] for i in pull(world)] == ['B-alpha1.v1', 'B-alpha2.v1']
    r = row(conn, 'B-alpha1.v1')
    assert (r['status'], r['attempts'], r['last_error']) == ('delivered', 2, None)


def test_a_serialise_type_error_also_marks_failed(world, conn, monkeypatch):
    decide(conn, world)

    def broken(row, project):
        raise TypeError('Object of type set is not JSON serializable')
    monkeypatch.setattr(deliveries, 'serialise', broken)
    assert pull(world) == []
    r = row(conn, 'B-alpha1.v1')
    assert (r['status'], r['attempts']) == ('failed', 1) and 'set' in r['last_error']


# --- acknowledging ---

def test_ack_moves_delivered_to_acknowledged_and_a_second_ack_changes_nothing(world, conn):
    decide(conn, world)
    pull(world)
    first = ack(world, 'B-alpha1.v1')
    assert first.status_code == 200
    assert first.get_json()['state'] == 'acknowledged'
    before = row(conn, 'B-alpha1.v1')
    world['app'].config['CLOCK'].now = START + datetime.timedelta(hours=1)
    second = ack(world, 'B-alpha1.v1')
    assert second.status_code == 200 and second.get_json() == first.get_json()
    assert row(conn, 'B-alpha1.v1') == before
    assert len(audit.rows(conn, 'delivery.ack')) == 1
    assert pull(world) == []


def test_ack_of_a_never_delivered_row_is_409(world, conn):
    decide(conn, world)
    r = ack(world, 'B-alpha1.v1')
    assert r.status_code == 409
    assert row(conn, 'B-alpha1.v1')['status'] == 'pending'


def test_ack_of_a_failed_row_is_409(world, conn):
    decide(conn, world)
    conn.execute("UPDATE decision SET body = 'x'")
    conn.commit()
    pull(world)
    assert ack(world, 'B-alpha1.v1').status_code == 409
    assert row(conn, 'B-alpha1.v1')['status'] == 'failed'


def test_unknown_or_other_projects_ids_are_404(world, conn):
    decide(conn, world)
    decide(conn, world, blocker_id='B-beta1', who='admin', project='beta')
    pull(world, world['pull_b'])
    assert ack(world, 'B-nope.v1').status_code == 404
    assert ack(world, 'B-beta1.v1').status_code == 404            # beta's row, alpha's token
    assert row(conn, 'B-beta1.v1')['status'] == 'delivered'
    assert ack(world, 'B-beta1.v1', world['pull_b']).status_code == 200


def test_the_endpoints_need_a_pull_token(world, conn):
    decide(conn, world)
    c = client(world['app'])
    assert c.get(PENDING_URL).status_code == 401
    assert c.get(PENDING_URL, headers={'Authorization': 'Bearer ' + world['ingest']}).status_code == 403
    assert c.post('/api/v1/deliveries/B-alpha1.v1/ack').status_code == 401
    assert ack(world, 'B-alpha1.v1', world['ingest']).status_code == 403
    assert row(conn, 'B-alpha1.v1')['attempts'] == 0
