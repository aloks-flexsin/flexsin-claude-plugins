"""Central task pool (autonomy plan, phase 2): unassigned tasks, taking other members' tasks when their person has no
active runner, Take it / Release / Reassign, merge-done reconciliation, released claims on stop, runner hand-over."""
import datetime
import sqlite3

import pytest

from central import accounts, audit, db as centraldb, personal, projects, runners, tasks

from .test_tasks import PW, START, add, api, clock, take, team  # noqa: F401  (fixtures)
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)
from .test_access import post, signed_in
from .test_account_admin import new_client

DEV_RUNNER = 'alok/bhawish-claude-auto-runner'
DEV2_RUNNER = 'ravi/ravi-claude-auto-runner'


@pytest.fixture
def pool(app, team, conn):
    """team plus a second developer (dev2) with a token and a page client."""
    accounts.create(conn, 'dev2@example.com', PW)
    projects.add_member(conn, team['alpha']['id'], 'dev2@example.com', 'developer', AT)
    team['accounts']['dev2'] = accounts.find_by_email(conn, 'dev2@example.com')
    team['clients']['dev2'] = signed_in(app, 'dev2@example.com')
    team['token']['dev2'] = personal.create_token(conn, team['accounts']['dev2']['id'], 'dev2', START)
    return team


def beat(conn, team, who, label, moment):
    runners.beat(conn, team['alpha'], team['accounts'][who], label, 'watching', None, 0, moment)


def row(conn, team, n):
    return tasks.get(conn, team['alpha']['id'], n)


def takes(conn):
    return [r['detail'] for r in audit.rows(conn, 'task.take')]


# ---------- adding to the pool ----------

def test_a_developer_adds_a_task_to_the_pool_and_any_runner_takes_it(app, pool, conn):
    n = add(pool, assignee='pool')
    t = row(conn, pool, n)
    assert t['assignee_id'] is None and t['assignee_email'] is None and t['status'] == 'open'
    page = pool['clients']['viewer'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'The pool' in page
    assert 'Unassigned' in pool['clients']['viewer'].get('/projects/alpha/tasks?view=list&state=open').get_data(as_text=True)
    got = take(app, pool, runner=DEV2_RUNNER, who='dev2')
    assert got['task']['number'] == n and got['task']['assignee'] == 'dev2@example.com'
    assert row(conn, pool, n)['status'] == 'planning'
    assert takes(conn) == [{'number': n, 'from': None, 'to': 'dev2@example.com', 'reason': 'unassigned',
                            'runner': DEV2_RUNNER, 'via': 'runner'}]


def test_viewers_and_supervisors_never_take_from_the_pool(app, pool, conn):
    add(pool, assignee='pool')
    assert take(app, pool, who='viewer').get('task') is None
    assert tasks.take_next(conn, pool['alpha'], pool['accounts']['viewer'], 'v', START) == (None, None)
    sup = accounts.create(conn, 'sup@example.com', PW, role='supervisor')
    projects.add_member(conn, pool['alpha']['id'], 'sup@example.com', 'manager', AT)
    sup_token = personal.create_token(conn, sup['id'], 'sup', START)
    assert api(app, 'POST', '/api/v1/tasks/next', sup_token, {'runner': 'x'}).get_json()['task'] is None
    assert takes(conn) == []


def test_a_viewer_cannot_add_to_the_pool(pool, conn):
    resp = post(pool['clients']['viewer'], '/projects/alpha/tasks', title='x', assignee='pool')
    assert resp.status_code == 403 and conn.query('SELECT * FROM project_task') == []
    with pytest.raises(tasks.TaskError):
        tasks.create(conn, pool['alpha'], pool['accounts']['dev'], None, 't', '', START, runner='r')


def test_supervisor_adds_a_task_to_the_pool(app, pool, conn):
    sup = accounts.create(conn, 'sup@example.com', PW, role='supervisor')
    token = personal.create_token(conn, sup['id'], 'sup', START)
    resp = new_client(app).post('/api/v1/supervisor/tasks?project=alpha', json={'title': 'Fix it', 'from': 'review #3'},
                                headers={'Authorization': 'Bearer ' + token})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert resp.get_json()['task']['assignee'] is None
    bad = new_client(app).post('/api/v1/supervisor/tasks?project=alpha',
                               json={'title': 'x', 'from': 'y', 'assignee': 'nobody@example.com'},
                               headers={'Authorization': 'Bearer ' + token})
    assert bad.status_code == 400


# ---------- order and who may take what ----------

def test_own_tasks_come_before_the_pool(app, pool, conn):
    add(pool, title='pool', assignee='pool', priority='1')
    mine = add(pool, title='mine', who='dev2', priority='9')
    assert take(app, pool, runner=DEV2_RUNNER, who='dev2')['task']['number'] == mine


def test_another_persons_task_waits_while_their_runner_is_active(app, pool, conn, clock):
    n = add(pool)                                              # dev's task
    beat(conn, pool, 'dev', DEV_RUNNER, START)
    assert take(app, pool, runner=DEV2_RUNNER, who='dev2')['task'] is None
    clock.now = START + runners.SILENT_AFTER + datetime.timedelta(minutes=1)   # dev's watcher went silent
    got = take(app, pool, runner=DEV2_RUNNER, who='dev2')
    assert got['task']['number'] == n and row(conn, pool, n)['assignee_email'] == 'dev2@example.com'
    assert takes(conn)[0]['reason'] == 'no active runner' and takes(conn)[0]['from'] == 'dev@example.com'


def test_a_person_without_any_runner_has_their_ready_tasks_taken(app, pool, conn):
    n = add(pool)
    assert take(app, pool, runner=DEV2_RUNNER, who='dev2')['task']['number'] == n


def test_a_runner_asked_to_stop_does_not_count_as_watching(app, pool, conn):
    n = add(pool)
    beat(conn, pool, 'dev', DEV_RUNNER, START)
    r = runners.get(conn, pool['alpha']['id'], DEV_RUNNER)
    runners.set_control(conn, r, 'stop', pool['accounts']['dev'], START)
    conn.commit()
    assert take(app, pool, runner=DEV2_RUNNER, who='dev2')['task']['number'] == n


def test_an_expired_claim_is_taken_even_while_its_person_watches(app, pool, conn, clock):
    n = add(pool)
    assert take(app, pool, runner=DEV_RUNNER)['task']['number'] == n
    later = START + tasks.CLAIM_TTL + datetime.timedelta(minutes=1)
    clock.now = later
    beat(conn, pool, 'dev', 'alok/other-claude-auto-runner', later)     # dev watches elsewhere; this claim is quiet
    got = take(app, pool, runner=DEV2_RUNNER, who='dev2')
    assert got['task']['number'] == n and takes(conn)[-1]['reason'] == 'claim expired'
    # the old runner's claim is gone
    assert api(app, 'POST', '/api/v1/tasks/%d/heartbeat' % n, pool['token']['dev'], {'claim': 'x'}).status_code == 409


def test_a_live_claim_is_never_taken_from_the_pool(app, pool, conn):
    n = add(pool)
    assert take(app, pool, runner=DEV_RUNNER)['task']['number'] == n
    assert take(app, pool, runner=DEV2_RUNNER, who='dev2')['task'] is None


# ---------- stopped runs and merges ----------

def build(app, team, conn, n, who='dev', runner=DEV_RUNNER):
    """Move task n straight to building with a claim -> the claim."""
    conn.execute("UPDATE project_task SET status = 'approved', plan = 'p', items = '- [ ] **A1 x.**' WHERE number = ?", (n,))
    conn.commit()
    got = take(app, team, runner=runner, who=who)
    assert got['task']['number'] == n and got['action'] == 'build'
    return got['claim']


def test_report_stopped_releases_the_claim_and_keeps_branch_and_pr(app, pool, conn, clock):
    n = add(pool)
    claim = build(app, pool, conn, n)
    resp = api(app, 'POST', '/api/v1/tasks/%d/report' % n, pool['token']['dev'],
               {'claim': claim, 'result': 'stopped', 'branch': 'alok/x/feat/y', 'pr_url': 'https://github.com/o/r/pull/7',
                'note': 'usage limit'})
    assert resp.status_code == 200
    t = row(conn, pool, n)
    assert (t['status'], t['claim_id'], t['branch'], t['pr_url'], t['note']) == (
        'approved', None, 'alok/x/feat/y', 'https://github.com/o/r/pull/7', 'usage limit')
    assert api(app, 'POST', '/api/v1/tasks/%d/heartbeat' % n, pool['token']['dev'], {'claim': claim}).status_code == 409
    # another member's runner picks it up (dev has no active runner) and continues on the same branch
    got = take(app, pool, runner=DEV2_RUNNER, who='dev2')
    assert got['task']['number'] == n and got['action'] == 'build' and got['task']['branch'] == 'alok/x/feat/y'


def test_merge_done_marks_the_task_done(app, pool, conn):
    sup = accounts.create(conn, 'sup@example.com', PW, role='supervisor')
    token = personal.create_token(conn, sup['id'], 'sup', START)
    n = add(pool)
    build(app, pool, conn, n)

    def merge(phase):
        return new_client(app).post('/api/v1/supervisor/tasks/%d/merge?project=alpha' % n,
                                    json={'phase': phase, 'sha': 'abc'}, headers={'Authorization': 'Bearer ' + token})
    assert merge('decided').status_code == 200
    assert merge('done').get_json()['task']['status'] == 'done'
    t = row(conn, pool, n)
    assert t['status'] == 'done' and t['claim_id'] is None and t['merged_at']
    assert [r['detail']['via'] for r in audit.rows(conn, 'task.done')] == ['merge']
    assert merge('done').get_json()['already'] is True and len(audit.rows(conn, 'task.done')) == 1


# ---------- the task page: Take it, Release, Reassign ----------

def test_take_it_on_the_page(app, pool, conn):
    n = add(pool)
    page = pool['clients']['dev2'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'Take it' in page and 'Reassign or release' not in page
    assert 'Take it' not in pool['clients']['dev'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'Take it' not in pool['clients']['viewer'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    asked = post(pool['clients']['dev2'], '/projects/alpha/tasks/%d/take' % n)
    assert asked.status_code == 200 and row(conn, pool, n)['assignee_email'] == 'dev@example.com'   # asks first
    assert post(pool['clients']['dev2'], '/projects/alpha/tasks/%d/take' % n, confirm='1').status_code == 302
    assert row(conn, pool, n)['assignee_email'] == 'dev2@example.com'
    assert takes(conn)[-1] == {'number': n, 'from': 'dev@example.com', 'to': 'dev2@example.com',
                               'reason': 'taken on the task page', 'via': 'page'}
    assert post(pool['clients']['viewer'], '/projects/alpha/tasks/%d/take' % n, confirm='1').status_code == 409


def test_take_it_is_refused_while_a_runner_holds_the_task(app, pool, conn):
    n = add(pool)
    take(app, pool, runner=DEV_RUNNER)
    assert 'Take it' not in pool['clients']['dev2'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert post(pool['clients']['dev2'], '/projects/alpha/tasks/%d/take' % n, confirm='1').status_code == 409
    assert row(conn, pool, n)['assignee_email'] == 'dev@example.com'


@pytest.mark.parametrize('who,allowed', [('dev', True), ('manager', True), ('owner', True), ('admin', True),
                                         ('dev2', False), ('viewer', False)])
def test_release_is_for_the_assignee_and_managers(app, pool, conn, who, allowed):
    n = add(pool)
    take(app, pool, runner=DEV_RUNNER)
    page = pool['clients'][who].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert ('Reassign or release' in page) == allowed
    resp = post(pool['clients'][who], '/projects/alpha/tasks/%d/release' % n, confirm='1')
    assert resp.status_code == (302 if allowed else 404)
    t = row(conn, pool, n)
    assert (t['claim_id'] is None) == allowed and t['assignee_email'] == 'dev@example.com'
    if allowed:
        assert t['status'] == 'open' and len(audit.rows(conn, 'task.release')) == 1


def test_reassign_to_a_member_or_back_to_the_pool(app, pool, conn):
    n = add(pool)
    take(app, pool, runner=DEV_RUNNER)
    dev2 = pool['accounts']['dev2']['id']
    assert post(pool['clients']['dev'], '/projects/alpha/tasks/%d/release' % n, assignee=dev2, confirm='1').status_code == 302
    assert row(conn, pool, n)['assignee_email'] == 'dev2@example.com'
    assert post(pool['clients']['dev2'], '/projects/alpha/tasks/%d/release' % n, assignee='pool', confirm='1').status_code == 302
    t = row(conn, pool, n)
    assert t['assignee_id'] is None and t['claim_id'] is None
    detail = audit.rows(conn, 'task.reassign')[-1]['detail']
    assert detail['pool'] is True and detail['from'] == 'dev2@example.com' and detail['to'] is None
    # a pool task is managed by managers, not by an ordinary developer
    assert post(pool['clients']['dev'], '/projects/alpha/tasks/%d/release' % n, confirm='1').status_code == 404
    assert post(pool['clients']['manager'], '/projects/alpha/tasks/%d/release' % n,
                assignee=pool['accounts']['viewer']['id'], confirm='1').status_code == 400
    activity = pool['clients']['viewer'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'gave this task to the pool' in activity


# ---------- supervisor API: release and reassign ----------

@pytest.fixture
def sup(conn, pool):
    s = accounts.create(conn, 'sup@example.com', PW, role='supervisor')
    return personal.create_token(conn, s['id'], 'sup', START)


def sup_call(app, token, n, action, body):
    return new_client(app).post('/api/v1/supervisor/tasks/%d/%s?project=alpha' % (n, action), json=body,
                                headers={'Authorization': 'Bearer ' + token})


def test_supervisor_releases_a_claim(app, pool, conn, sup):
    n = add(pool)
    claim = build(app, pool, conn, n)
    assert sup_call(app, pool['token']['dev'], n, 'release', {}).status_code == 403
    resp = sup_call(app, sup, n, 'release', {'reason': 'stalled'})
    assert resp.status_code == 200 and resp.get_json()['changed'] is True
    t = row(conn, pool, n)
    assert (t['status'], t['claim_id'], t['assignee_email']) == ('approved', None, 'dev@example.com')
    assert api(app, 'POST', '/api/v1/tasks/%d/heartbeat' % n, pool['token']['dev'], {'claim': claim}).status_code == 409
    assert audit.rows(conn, 'supervisor.task.release')[0]['detail']['reason'] == 'stalled'
    assert sup_call(app, sup, n, 'release', {}).get_json()['changed'] is False
    assert sup_call(app, sup, 99, 'release', {}).status_code == 404


def test_supervisor_reassigns_to_a_member_or_the_pool(app, pool, conn, sup):
    n = add(pool)
    take(app, pool, runner=DEV_RUNNER)
    assert sup_call(app, sup, n, 'reassign', {}).status_code == 400
    assert sup_call(app, sup, n, 'reassign', {'assignee': 'viewer@example.com'}).status_code == 400
    resp = sup_call(app, sup, n, 'reassign', {'assignee': 'dev2@example.com', 'reason': 'dev is away'})
    assert resp.status_code == 200 and resp.get_json()['task']['assignee'] == 'dev2@example.com'
    assert sup_call(app, sup, n, 'reassign', {'assignee': 'dev2@example.com'}).get_json()['changed'] is False
    assert sup_call(app, sup, n, 'reassign', {'assignee': None}).get_json()['task']['assignee'] is None
    details = [r['detail'] for r in audit.rows(conn, 'supervisor.task.reassign')]
    assert [(d['from'], d['to']) for d in details] == [('dev@example.com', 'dev2@example.com'), ('dev2@example.com', None)]


# ---------- runner hand-over ----------

def test_a_runner_label_is_handed_over_by_its_person_or_a_manager(app, pool, conn):
    beat(conn, pool, 'dev', DEV_RUNNER, START)
    with pytest.raises(runners.RunnerError) as e:
        beat(conn, pool, 'dev2', DEV_RUNNER, START)
    assert e.value.status == 403
    r = runners.get(conn, pool['alpha']['id'], DEV_RUNNER)
    url = '/projects/alpha/runners/%s/hand-over' % r['id']
    dev2 = pool['accounts']['dev2']['id']
    assert post(pool['clients']['dev2'], url, account=dev2, confirm='1').status_code == 404
    assert post(pool['clients']['manager'], url, account=pool['accounts']['viewer']['id'], confirm='1').status_code == 400
    assert post(pool['clients']['manager'], url, account=dev2).status_code == 200            # asks first
    assert post(pool['clients']['manager'], url, account=dev2, confirm='1').status_code == 302
    beat(conn, pool, 'dev2', DEV_RUNNER, START)
    with pytest.raises(runners.RunnerError):
        beat(conn, pool, 'dev', DEV_RUNNER, START)
    assert audit.rows(conn, 'runner.reassign')[0]['detail'] == {'label': DEV_RUNNER, 'from': 'dev@example.com',
                                                               'to': 'dev2@example.com'}


def test_runs_page_offers_give_runner_to_its_person_and_managers(app, pool, conn):
    beat(conn, pool, 'dev', DEV_RUNNER, START)
    for who, shown in (('dev', True), ('manager', True), ('dev2', False), ('viewer', False)):
        page = pool['clients'][who].get('/projects/alpha/runs').get_data(as_text=True)
        assert ('Give runner' in page) == shown, who


# ---------- schema ----------

def test_an_old_database_gets_an_optional_assignee(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with open(centraldb.SCHEMA_PATH, encoding='utf-8') as f:
        script = f.read().replace('assignee_id VARCHAR(64) REFERENCES account (id),',
                                  'assignee_id VARCHAR(64) NOT NULL REFERENCES account (id),')
    old = sqlite3.connect(str(path))
    old.executescript(script)
    old.execute("INSERT INTO account (id, email, password_hash, role, created_at) VALUES ('a', 'a@x', '!', 'user', 't')")
    old.execute("INSERT INTO project (id, slug, name, created_at) VALUES ('p', 'p', 'P', 't')")
    old.execute("INSERT INTO project_task (id, project_id, number, title, assignee_id, status, created_at, updated_at)"
                " VALUES ('t1', 'p', 1, 'Kept', 'a', 'open', 't', 't')")
    old.commit()
    old.close()
    url = 'sqlite:///' + path.as_posix()
    centraldb.init_db(url)
    c = centraldb.connect(url)
    try:
        info = {r['name']: r for r in c.query('PRAGMA table_info(project_task)')}
        assert info['assignee_id']['notnull'] == 0
        assert [r['title'] for r in c.query('SELECT title FROM project_task')] == ['Kept']
        assert centraldb.task_assignee_optional(c) is False                    # once is enough
    finally:
        c.close()
