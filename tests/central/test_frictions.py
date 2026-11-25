"""Central service: runner frictions are counted at ingest and when a plan is sent back (decision record 0011, R6)."""
import datetime
import json

import pytest

from central import accounts, audit, db, frictions, projects, tasks

from .test_access import PW, new_client, post, signed_in
from .test_ingest import START, clock, conn, event, friction, send, world  # noqa: F401  (fixtures)


def counts(conn, sig):
    return conn.query('SELECT * FROM friction_count WHERE signature = ? ORDER BY project_id', (sig,))


def days(conn, sig):
    return conn.query('SELECT * FROM friction_day WHERE signature = ? ORDER BY project_id, day', (sig,))


def signatures(conn):
    return conn.query('SELECT * FROM friction_signature ORDER BY signature')


def test_three_events_from_one_project_count_three(world, conn, clock):
    for minutes in (0, 5, 30):
        clock.now = START + datetime.timedelta(minutes=minutes)
        assert send(world['app'], world['token'], friction()).status_code == 200
    [row] = counts(conn, 'retry:exit_1')
    assert row['project_id'] == world['alpha']['id'] and row['count'] == 3
    assert row['first_seen'] == '2026-10-31T09:00:00Z' and row['last_seen'] == '2026-10-31T09:30:00Z'
    [day] = days(conn, 'retry:exit_1')
    assert (day['day'], day['count']) == ('2026-10-31', 3)
    [sig] = signatures(conn)
    assert (sig['signature'], sig['kind'], sig['subject'], sig['state']) == ('retry:exit_1', 'retry', 'exit_1', 'watching')
    assert sig['changed_at'] is None and sig['task_number'] is None


def test_days_are_the_services_utc_days(world, conn, clock):
    send(world['app'], world['token'], friction())
    clock.now = START + datetime.timedelta(days=1)
    send(world['app'], world['token'], friction())
    assert [(d['day'], d['count']) for d in days(conn, 'retry:exit_1')] == [('2026-10-31', 1), ('2026-11-01', 1)]
    assert counts(conn, 'retry:exit_1')[0]['count'] == 2


def test_the_same_event_twice_counts_once(world, conn):
    e = friction()
    assert send(world['app'], world['token'], e).get_json()['accepted'] == 1
    assert send(world['app'], world['token'], e).get_json()['duplicates'] == 1
    assert send(world['app'], world['token'], [e, e]).get_json()['duplicates'] == 2
    assert counts(conn, 'retry:exit_1')[0]['count'] == 1
    assert days(conn, 'retry:exit_1')[0]['count'] == 1


def test_two_projects_make_two_counts_and_one_signature(world, conn):
    send(world['app'], world['token'], friction(kind='permission_denied', subject='Bash:sed'))
    send(world['app'], world['beta_token'], friction(item=None, kind='permission_denied', subject='Bash:sed')
         | {'project': 'beta'})
    rows = counts(conn, 'permission_denied:Bash:sed')
    assert sorted(r['project_id'] for r in rows) == sorted([world['alpha']['id'], world['beta']['id']])
    assert [r['count'] for r in rows] == [1, 1]
    assert [(s['signature'], s['state']) for s in signatures(conn)] == [('permission_denied:Bash:sed', 'watching')]


def test_an_empty_subject_is_the_kind_alone(world, conn):
    send(world['app'], world['token'], friction(kind='plan_changes', subject=''))
    assert counts(conn, 'plan_changes')[0]['count'] == 1


def test_other_events_are_not_counted(world, conn):
    send(world['app'], world['token'], event())
    assert conn.query('SELECT * FROM friction_count') == []


def test_an_opted_out_project_stores_events_but_does_not_count(world, conn):
    frictions.set_sharing(conn, world['alpha']['id'], False)
    conn.commit()
    e = friction()
    assert send(world['app'], world['token'], e).get_json()['accepted'] == 1
    assert conn.query_one('SELECT id FROM event WHERE id = ?', (e['id'],))
    assert conn.query('SELECT * FROM friction_count') == [] and conn.query('SELECT * FROM friction_day') == []
    assert signatures(conn) == []


def test_a_signature_row_keeps_its_state(world, conn):
    send(world['app'], world['token'], friction())
    conn.execute("UPDATE friction_signature SET state = 'muted', reason = 'known'")
    conn.commit()
    send(world['app'], world['token'], friction())
    [sig] = signatures(conn)
    assert (sig['state'], sig['reason']) == ('muted', 'known')
    assert counts(conn, 'retry:exit_1')[0]['count'] == 2


# --- a "changes" decision counts plan_changes ---

@pytest.fixture
def people(world, conn):
    """alpha (from the ingest world) with an owner, a manager and a viewer; beta's owner is a stranger to alpha."""
    accts = {who: accounts.create(conn, '%s@example.com' % who, PW) for who in ('owner', 'manager', 'stranger')}
    for who in ('owner', 'manager'):
        projects.add_member(conn, world['alpha']['id'], '%s@example.com' % who, who, '2026-10-31T09:00:00Z')
    projects.add_member(conn, world['beta']['id'], 'stranger@example.com', 'owner', '2026-10-31T09:00:00Z')
    conn.commit()
    clients = {who: signed_in(world['app'], '%s@example.com' % who) for who in accts}
    return dict(world, accounts=accts, clients=clients)


def waiting_task(conn, people):
    row = tasks.create(conn, people['alpha'], people['accounts']['owner'], people['accounts']['owner'], 'CSV export',
                       'Export the table.', START, status='plan_review', plan='## Intent\nCSV.', items='- [ ] E1')
    conn.commit()
    return row['number']


@pytest.mark.parametrize('decision, counted', [('changes', 1), ('approve', 0), ('reject', 0)])
def test_a_changes_decision_counts_plan_changes(people, conn, decision, counted):
    n = waiting_task(conn, people)
    resp = post(people['clients']['owner'], '/projects/alpha/tasks/%d/decide' % n, decision=decision,
                comment='Split the export.', confirm='1')
    assert resp.status_code == 302, resp.get_data(as_text=True)
    rows = counts(conn, 'plan_changes')
    assert [r['count'] for r in rows] == ([1] if counted else [])
    if counted:
        assert rows[0]['project_id'] == people['alpha']['id']
        assert days(conn, 'plan_changes')[0]['day'] == '2026-10-31'
        assert [(s['kind'], s['subject'], s['state']) for s in signatures(conn)] == [('plan_changes', '', 'watching')]


def test_a_changes_decision_of_an_opted_out_project_is_not_counted(people, conn):
    frictions.set_sharing(conn, people['alpha']['id'], False)
    n = waiting_task(conn, people)
    post(people['clients']['owner'], '/projects/alpha/tasks/%d/decide' % n, decision='changes', comment='Smaller.')
    assert tasks.get(conn, people['alpha']['id'], n)['status'] == 'changes_requested'
    assert counts(conn, 'plan_changes') == []


# --- the Share setting on Settings › Repository ---

PAGE = '/projects/alpha/settings/repository'
SWITCH = '/projects/alpha/share-frictions'


def sharing(conn, project):
    return projects.get_by_slug(conn, project['slug'])['share_frictions']


def test_sharing_is_on_by_default(people, conn):
    assert sharing(conn, people['alpha']) == 1
    page = people['clients']['owner'].get(PAGE).get_data(as_text=True)
    assert 'Share runner frictions with the auto-runner project' in page and SWITCH in page


def test_an_owner_switches_it_off_and_on_with_audit_rows(people, conn):
    owner = people['clients']['owner']
    resp = post(owner, SWITCH, share='0')
    assert resp.status_code == 302 and sharing(conn, people['alpha']) == 0
    [row] = audit.rows(conn, 'project.share_frictions')
    detail = json.loads(row['detail']) if isinstance(row['detail'], str) else row['detail']
    assert detail == {'from': True, 'to': False}
    assert row['account_id'] == people['accounts']['owner']['id'] and row['project_id'] == people['alpha']['id']
    assert 'Stop sharing' not in owner.get(PAGE).get_data(as_text=True)
    assert post(owner, SWITCH, share='1').status_code == 302 and sharing(conn, people['alpha']) == 1
    assert len(audit.rows(conn, 'project.share_frictions')) == 2


def test_a_bad_value_is_400(people, conn):
    assert post(people['clients']['owner'], SWITCH, share='maybe').status_code == 400
    assert sharing(conn, people['alpha']) == 1 and audit.rows(conn, 'project.share_frictions') == []


@pytest.mark.parametrize('who', ['manager', 'stranger', 'anonymous'])
def test_others_are_refused(people, conn, who):
    client = new_client(people['app']) if who == 'anonymous' else people['clients'][who]
    assert post(client, SWITCH, share='0').status_code == 404
    assert sharing(conn, people['alpha']) == 1 and audit.rows(conn, 'project.share_frictions') == []
    if who == 'manager':
        page = client.get(PAGE).get_data(as_text=True)
        assert 'Share runner frictions' in page and SWITCH not in page


def test_a_post_without_the_csrf_token_is_refused(people, conn):
    resp = people['clients']['owner'].post(SWITCH, data={'share': '0'})
    assert resp.status_code in (400, 403)
    assert sharing(conn, people['alpha']) == 1 and audit.rows(conn, 'project.share_frictions') == []


def test_the_column_is_added_to_an_older_database_and_defaults_on(tmp_path):
    url = 'sqlite:///%s' % (tmp_path / 'old.db')
    old = db.connect(url)
    old.execute('CREATE TABLE project (id VARCHAR(64) PRIMARY KEY, slug VARCHAR(64) NOT NULL UNIQUE,'
                ' name VARCHAR(200) NOT NULL, repo_url VARCHAR(500), repo_key VARCHAR(500),'
                ' created_at VARCHAR(32) NOT NULL)')
    old.execute("INSERT INTO project (id, slug, name, created_at) VALUES ('p1', 'old', 'Old', '2026-01-01T00:00:00Z')")
    old.commit()
    old.close()
    added = db.init_db(url)
    assert 'project.share_frictions' in added
    c = db.connect(url)
    assert c.query_one("SELECT share_frictions FROM project WHERE id = 'p1'")['share_frictions'] == 1
    c.close()
