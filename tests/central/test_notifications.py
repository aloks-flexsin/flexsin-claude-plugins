"""Central service: in-app notifications are created for the people something waits on, and resolved once it no longer
waits (queue item N1)."""
import datetime
import json
import uuid

import pytest

from central import accounts, db, deliveries, notifications, projects, tokens

from .test_access import PW, new_client, post
from .test_repo_url import AT, OLD_PROJECT, conn, world  # noqa: F401  (fixtures)
from .test_tasks import START, add, api, clock, decide, plan, take, team  # noqa: F401  (fixtures)

PEOPLE = ('dev', 'owner', 'manager', 'viewer', 'admin', 'stranger')


def rows(conn, team, who, kind=None):
    out = notifications.for_account(conn, team['accounts'][who]['id'])
    return [r for r in out if kind is None or r['kind'] == kind]


def waiting(conn, team, kind):
    """{person: [text, ...]} of unresolved notifications of `kind`, for everyone in PEOPLE who has any."""
    out = {}
    for who in PEOPLE:
        texts = [r['text'] for r in rows(conn, team, who, kind) if r['resolved_at'] is None]
        if texts:
            out[who] = texts
    return out


@pytest.fixture(autouse=True)
def stranger(team, conn):
    """An account that is a member of no project."""
    team['accounts']['stranger'] = accounts.create(conn, 'stranger@example.com', PW)


def propose(app, team, title='Fix login'):
    body = api(app, 'POST', '/api/v1/tasks', team['token']['dev'],
               {'runner': 'laptop', 'title': title, 'plan': '## Intent\nFix.', 'items': '- [ ] **F1 Fix.**',
                'approval': 'page'}).get_json()
    return body['task']['number'], body['claim']


def raise_blocker(app, team, conn, blocker_id='B-greet', question='Which greeting?'):
    raw, _ = tokens.create(conn, team['alpha']['id'], ['ingest'], None, AT)
    event = {'schema': 'event.v1', 'id': uuid.uuid4().hex, 'time': '2026-11-23T09:00:00+00:00', 'run': '20261123-0900',
             'project': 'alpha', 'type': 'blocker', 'item': 'D1', 'evidence_class': 'agent_reported',
             'detail': {'action': 'raised', 'id': blocker_id, 'blocker': {'question': question}}}
    resp = new_client(app).post('/api/v1/events', data=json.dumps(event),
                                headers={'Authorization': 'Bearer ' + raw, 'Content-Type': 'application/json'})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return raw, event


# ---------- plan ----------

def test_a_posted_plan_reaches_the_assignee_and_managers_and_owners_only(app, team, conn):
    n = add(team)
    claim = take(app, team)['claim']
    assert plan(app, team, n, claim).status_code == 200
    text = 'Plan waiting: #%d CSV export' % n
    assert waiting(conn, team, 'plan') == {'dev': [text], 'owner': [text], 'manager': [text]}
    row = rows(conn, team, 'manager', 'plan')[0]
    assert row['link'] == '/projects/alpha/tasks/%d' % n and row['project_slug'] == 'alpha'
    assert notifications.unread_count(conn, team['accounts']['manager']['id']) == 1
    assert notifications.unread_count(conn, team['accounts']['admin']['id']) == 0    # a site admin, not a member


def test_a_plan_proposed_for_page_approval_notifies_and_a_terminal_one_does_not(app, team, conn):
    n, _ = propose(app, team)
    assert waiting(conn, team, 'plan') == {who: ['Plan waiting: #%d Fix login' % n] for who in ('dev', 'owner', 'manager')}
    api(app, 'POST', '/api/v1/tasks', team['token']['dev'], {'runner': 'laptop', 'title': 'Other', 'plan': 'p',
                                                             'items': '- [ ] **F2 x.**'})
    assert len(rows(conn, team, 'owner')) == 1


def test_any_decision_resolves_the_plan(app, team, conn):
    n, _ = propose(app, team)
    assert decide(team, n, 'approve', who='manager').status_code == 302
    assert waiting(conn, team, 'plan') == {}
    assert all(r['resolved_at'] == '2026-11-23T09:00:00Z' for r in rows(conn, team, 'owner', 'plan'))
    assert notifications.unread_count(conn, team['accounts']['owner']['id']) == 0
    n2, _ = propose(app, team, 'Second')
    assert decide(team, n2, 'reject', who='owner').status_code == 302
    assert waiting(conn, team, 'plan') == {}


# ---------- changes ----------

def test_changes_reach_the_assignee_only_and_the_next_plan_resolves_them(app, team, conn, clock):
    n = add(team)
    claim = take(app, team)['claim']
    plan(app, team, n, claim)
    assert decide(team, n, 'changes', who='manager', comment='Add a header row.').status_code == 302
    assert waiting(conn, team, 'changes') == {'dev': ['Changes asked on #%d CSV export' % n]}
    assert rows(conn, team, 'dev', 'changes')[0]['link'] == '/projects/alpha/tasks/%d' % n
    assert waiting(conn, team, 'plan') == {}                          # the decision resolved the plan

    clock.now = START + datetime.timedelta(minutes=10)
    claim = take(app, team)['claim']
    assert plan(app, team, n, claim).status_code == 200               # the revised plan
    assert waiting(conn, team, 'changes') == {}
    assert sorted(waiting(conn, team, 'plan')) == ['dev', 'manager', 'owner']     # waits again, a new row each
    assert len(rows(conn, team, 'owner', 'plan')) == 2


# ---------- assigned ----------

def test_an_admin_assigning_to_someone_else_notifies_them(app, team, conn):
    n = add(team, who='admin', assignee=team['accounts']['dev']['id'], title='Pick colours')
    assert waiting(conn, team, 'assigned') == {'dev': ['Assigned to you: #%d Pick colours' % n]}
    assert rows(conn, team, 'dev', 'assigned')[0]['link'] == '/projects/alpha/tasks/%d' % n
    add(team, who='dev', title='Mine')                                 # a task for yourself notifies no one
    assert len(rows(conn, team, 'dev')) == 1


def test_reassigning_through_release_notifies_the_new_assignee_and_resolves_plan_and_changes(app, team, conn):
    n, _ = propose(app, team)
    resp = post(team['clients']['admin'], '/projects/alpha/tasks/%d/release' % n, confirm='1',
                assignee=team['accounts']['manager']['id'])
    assert resp.status_code == 302
    assert waiting(conn, team, 'plan') == {}
    assert waiting(conn, team, 'assigned') == {'manager': ['Assigned to you: #%d Fix login' % n]}

    n2 = add(team, title='Second')
    claim = take(app, team)['claim']
    plan(app, team, n2, claim)
    decide(team, n2, 'changes', who='owner', comment='Smaller.')
    assert waiting(conn, team, 'changes') == {'dev': ['Changes asked on #%d Second' % n2]}
    post(team['clients']['admin'], '/projects/alpha/tasks/%d/release' % n2, confirm='1',
         assignee=team['accounts']['owner']['id'])
    assert waiting(conn, team, 'changes') == {}
    assert waiting(conn, team, 'assigned')['owner'] == ['Assigned to you: #%d Second' % n2]


def test_taking_a_task_back_without_a_new_person_keeps_a_waiting_plan(app, team, conn):
    n, _ = propose(app, team)
    post(team['clients']['admin'], '/projects/alpha/tasks/%d/release' % n, confirm='1')
    assert sorted(waiting(conn, team, 'plan')) == ['dev', 'manager', 'owner']      # still plan_review: still waits


# ---------- blocker ----------

def test_a_new_blocker_reaches_managers_and_owners_and_an_answer_resolves_it(app, team, conn):
    raw, event = raise_blocker(app, team, conn)
    assert waiting(conn, team, 'blocker') == {'owner': ['Blocker waiting: Which greeting?'],
                                              'manager': ['Blocker waiting: Which greeting?']}
    assert rows(conn, team, 'owner', 'blocker')[0]['link'] == '/projects/alpha/blockers/B-greet'

    again = dict(event, id=uuid.uuid4().hex)                          # the runner raises the same blocker again
    resp = new_client(app).post('/api/v1/events', data=json.dumps(again),
                                headers={'Authorization': 'Bearer ' + raw, 'Content-Type': 'application/json'})
    assert resp.status_code == 200
    assert len(rows(conn, team, 'owner', 'blocker')) == 1

    deliveries.record_decision(conn, team['alpha'], 'B-greet', team['accounts']['manager'], option='Hello',
                               now=START)
    assert waiting(conn, team, 'blocker') == {}


# ---------- the store itself ----------

def test_notify_skips_duplicates_and_non_members(team, conn):
    ids = [team['accounts'][w]['id'] for w in ('dev', 'dev', 'admin', 'stranger', 'viewer')]
    first = notifications.notify(conn, ids, team['alpha'], 'assigned', 't', '/x', 'k1', START)
    assert first == [team['accounts']['dev']['id'], team['accounts']['viewer']['id']]
    assert notifications.notify(conn, ids, team['alpha'], 'assigned', 't', '/x', 'k1', START) == []
    assert notifications.resolve(conn, 'k1', START) == 2
    assert len(notifications.notify(conn, ids, team['alpha'], 'assigned', 't', '/x', 'k1', START)) == 2   # waits again
    projects.add_member(conn, team['alpha']['id'], 'stranger@example.com', 'viewer', AT)    # a member now: notified
    assert notifications.notify(conn, ids, team['alpha'], 'assigned', 't', '/x', 'k2', START) == \
        [team['accounts'][w]['id'] for w in ('dev', 'stranger', 'viewer')]


def test_init_db_adds_the_notification_table_to_an_existing_database(tmp_path):
    db_url = 'sqlite:///%s' % (tmp_path / 'old.db')                  # not the app's database, which is up to date
    old = db.connect(db_url)
    old.execute(OLD_PROJECT)
    old.execute("INSERT INTO project (id, slug, name, created_at) VALUES ('p1', 'old', 'Old', ?)", (AT,))
    old.commit()
    old.close()
    db.init_db(db_url)
    conn = db.connect(db_url)
    try:
        assert {'id', 'account_id', 'project_id', 'kind', 'text', 'link', 'dedupe', 'created_at', 'read_at',
                'resolved_at', 'push_state', 'push_detail'} == conn.columns('notification')
        assert conn.query_one("SELECT slug FROM project WHERE id = 'p1'")['slug'] == 'old'
    finally:
        conn.close()
    db.init_db(db_url)
