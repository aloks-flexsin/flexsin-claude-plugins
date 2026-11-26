import pytest

from central import accounts, audit, personal, projects, runners, tasks

from .test_tasks import PW, START, add, clock, decide, plan, take, team  # noqa: F401  (fixtures)
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)
from .test_account_admin import new_client


def get(app, path, token=None):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    return new_client(app).get(path, headers=headers)


@pytest.fixture
def sup_token(conn, team):
    sup = accounts.create(conn, 'sup@example.com', PW, role='supervisor')
    return personal.create_token(conn, sup['id'], 'sup', START)


def send(app, token, number, decision, project='alpha', **body):
    return new_client(app).post('/api/v1/supervisor/tasks/%d/plan-decision?project=%s' % (number, project),
                                json=dict(body, decision=decision), headers={'Authorization': 'Bearer ' + token})


@pytest.fixture
def member(conn, team, sup_token):
    projects.add_member(conn, team['alpha']['id'], 'sup@example.com', 'manager', START)


def manual_project(conn, team, slug='alpha'):
    """Manual mode (decision record 0012): the behaviour from before autonomy, pinned by the tests that use it."""
    projects.set_autonomy(conn, team[slug]['id'], 'manual')
    conn.commit()


def waiting(app, team, runner='alok/bhawish-claude-auto-runner'):
    n = add(team)
    assert plan(app, team, n, take(app, team, runner=runner)['claim']).status_code == 200
    return n


def test_state_is_read_only_and_supervisor_only(app, team, conn, sup_token):
    n = add(team)
    take(app, team)
    url = '/api/v1/supervisor/state?project=alpha'
    before = ([dict(r) for r in conn.query('SELECT * FROM project_task')], len(audit.rows(conn)))
    ok = get(app, url, sup_token)
    assert ok.status_code == 200
    body = ok.get_json()
    assert [t['number'] for t in body['tasks']] == [n] and body['project'] == 'alpha'
    assert 'claim' not in ok.get_data(as_text=True)
    assert {'plan', 'items', 'status', 'branch', 'pr_url', 'plan_rounds', 'merged_at'} <= set(body['tasks'][0])
    assert body['runners'] == [] and body['blockers'] == []
    assert get(app, url, team['token']['viewer']).status_code == 403
    admin_token = personal.create_token(conn, team['accounts']['admin']['id'], 'adm', START)
    assert get(app, url, admin_token).status_code == 200        # decision 0012 Phase 8: any developer+ may act as the stage
    assert get(app, url).status_code == 401
    assert get(app, '/api/v1/supervisor/state?project=nope', sup_token).status_code == 404
    assert get(app, '/api/v1/supervisor/state?project=nope', team['token']['dev']).status_code == 403
    assert new_client(app).post(url, headers={'Authorization': 'Bearer ' + sup_token}).status_code == 405
    assert ([dict(r) for r in conn.query('SELECT * FROM project_task')], len(audit.rows(conn))) == before


def test_whoami_returns_role(app, team, sup_token):
    assert get(app, '/api/v1/supervisor/whoami', sup_token).get_json()['role'] == 'supervisor'
    assert get(app, '/api/v1/supervisor/whoami', team['token']['dev']).get_json()['role'] == 'user'
    assert get(app, '/api/v1/supervisor/whoami').status_code == 401
    assert get(app, '/api/v1/supervisor/whoami', 'bogus').status_code == 401


def test_state_plan_rounds_counts_changes_rows(app, team, conn, sup_token, member):
    n, other = add(team), add(team, title='Other')
    for _ in range(2):
        claim = take(app, team)['claim']
        assert plan(app, team, n, claim).status_code == 200
        assert send(app, sup_token, n, 'changes', reasons=['again']).status_code == 200
    rows = {t['number']: t for t in get(app, '/api/v1/supervisor/state?project=alpha', sup_token).get_json()['tasks']}
    assert rows[n]['plan_rounds'] == 2 and rows[other]['plan_rounds'] == 0
    assert rows[n]['status'] == 'changes_requested' and rows[n]['merged_at'] is None


def test_merged_at_column_exists_and_is_migrated(conn):
    from central import db as centraldb
    assert ('project_task', 'merged_at', 'VARCHAR(32)') in centraldb.COLUMNS_ADDED
    assert 'merged_at' in conn.columns('project_task')


def rows(conn, action):
    return audit.rows(conn, action)


def test_approve_writes_the_audit_row_with_sha_and_reasons(app, team, conn, sup_token, member):
    n = waiting(app, team)
    resp = send(app, sup_token, n, 'approve', policy_sha='abc123', reasons=['all five checks passed'])
    assert resp.status_code == 200 and resp.get_json()['decision'] == 'approve' and resp.get_json()['task']['status'] == 'approved'
    [row] = rows(conn, 'supervisor.plan.approve')
    assert row['detail']['policy_sha'] == 'abc123' and row['detail']['reasons'] == ['all five checks passed']
    sup = accounts.find_by_email(conn, 'sup@example.com')
    assert tasks.get(conn, team['alpha']['id'], n)['decided_by'] == sup['id']


def test_changes_sends_the_reasons_back_as_the_comment(app, team, conn, sup_token, member):
    n = waiting(app, team)
    assert send(app, sup_token, n, 'changes', reasons=['item E1 has no Acceptance line']).status_code == 200
    row = tasks.get(conn, team['alpha']['id'], n)
    assert row['status'] == 'changes_requested' and 'no Acceptance' in row['decision_comment']
    assert send(app, sup_token, waiting(app, team, 'alok/second-claude-auto-runner'), 'changes').status_code == 400


def test_third_failing_plan_escalates_in_manual_mode(app, team, conn, sup_token, member):
    manual_project(conn, team)
    n = add(team)
    for _ in (1, 2):
        assert plan(app, team, n, take(app, team)['claim']).status_code == 200
        assert send(app, sup_token, n, 'changes', reasons=['restricted path']).get_json()['decision'] == 'changes'
    assert plan(app, team, n, take(app, team)['claim']).status_code == 200
    resp = send(app, sup_token, n, 'changes', reasons=['restricted path'])
    assert resp.status_code == 200 and resp.get_json()['decision'] == 'escalate' and resp.get_json()['converted']
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'plan_review'
    assert len(rows(conn, 'supervisor.plan.changes')) == 2 and len(rows(conn, 'supervisor.plan.escalate')) == 1
    state = get(app, '/api/v1/supervisor/state?project=alpha', sup_token).get_json()['tasks'][0]
    assert state['plan_rounds'] == 2 and state['plan_decided'] == 'escalate'


def test_rerun_does_not_duplicate_decision(app, team, conn, sup_token, member):
    n = waiting(app, team)
    first = send(app, sup_token, n, 'approve', policy_sha='s1')
    again = send(app, sup_token, n, 'approve', policy_sha='s1')
    assert first.get_json()['already'] is False and again.status_code == 200 and again.get_json()['already'] is True
    assert len(rows(conn, 'supervisor.plan.approve')) == 1
    m = waiting(app, team, 'alok/second-claude-auto-runner')
    send(app, sup_token, m, 'escalate', reasons=['a person decides'])
    assert send(app, sup_token, m, 'escalate').get_json()['already'] is True
    assert len(rows(conn, 'supervisor.plan.escalate')) == 1


def test_a_new_plan_post_is_decided_afresh(app, team, conn, sup_token, member):
    n = waiting(app, team)
    send(app, sup_token, n, 'changes', reasons=['x'])
    assert plan(app, team, n, take(app, team)['claim']).status_code == 200
    assert send(app, sup_token, n, 'approve').get_json()['already'] is False


def test_plan_decision_is_for_the_supervisor_or_a_developer_and_above(app, team, conn, sup_token, member):
    n = waiting(app, team)
    assert send(app, team['token']['viewer'], n, 'approve').status_code == 403
    url = '/api/v1/supervisor/tasks/%d/plan-decision?project=alpha' % n
    assert new_client(app).post(url, json={'decision': 'approve'}).status_code == 401
    assert send(app, sup_token, n, 'approve', project='nope').status_code == 404
    assert send(app, sup_token, n + 9, 'approve').status_code == 404
    assert send(app, sup_token, n, 'maybe').status_code == 400
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'plan_review' and rows(conn, 'supervisor.plan.approve') == []


def test_a_supervisor_who_is_not_a_member_cannot_decide_in_manual_mode(app, team, conn, sup_token):
    manual_project(conn, team)
    n = waiting(app, team)
    assert send(app, sup_token, n, 'approve').status_code == 403
    assert rows(conn, 'supervisor.plan.approve') == []


def test_escalate_needs_a_waiting_plan_and_keeps_it_waiting(app, team, conn, sup_token, member):
    n = waiting(app, team)
    assert send(app, sup_token, add(team), 'escalate').status_code == 409
    assert send(app, sup_token, n, 'escalate', reasons=['Needs a person']).status_code == 200
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'plan_review'


# ---------- the supervisor adds tasks (decision record 0010, 6a) ----------

def add_as_supervisor(app, token, project='alpha', **body):
    body.setdefault('title', 'Runner tests each item with its own tests')
    body.setdefault('from', "the user's request on 2026-11-26")
    body.setdefault('assignee', 'dev@example.com')
    return new_client(app).post('/api/v1/supervisor/tasks?project=%s' % project, json=body,
                                headers={'Authorization': 'Bearer ' + token})


def test_supervisor_adds_an_open_task_that_says_where_it_comes_from(app, team, conn, sup_token):
    resp = add_as_supervisor(app, sup_token, description='Run only the tests an item can affect.')
    assert resp.status_code == 201
    task = resp.get_json()['task']
    assert task['status'] == 'open' and task['assignee'] == 'dev@example.com'
    row = tasks.get(conn, team['alpha']['id'], task['number'])
    assert row['description'].endswith("From: the user's request on 2026-11-26")
    assert row['claim_id'] is None
    added = rows(conn, 'supervisor.task.add')
    assert len(added) == 1 and len(rows(conn, 'task.create')) == 1
    assert take(app, team)['task']['number'] == task['number']      # the assignee's runner takes and plans it


def test_adding_needs_a_source_a_runnable_assignee_and_a_supervisor(app, team, conn, sup_token):
    before = len(conn.query('SELECT * FROM project_task'))
    assert add_as_supervisor(app, sup_token, **{'from': '  '}).status_code == 400
    assert add_as_supervisor(app, sup_token, assignee='viewer@example.com').status_code == 400
    assert add_as_supervisor(app, sup_token, assignee='nobody@example.com').status_code == 400
    assert add_as_supervisor(app, sup_token, assignee='sup@example.com').status_code == 400
    assert add_as_supervisor(app, sup_token, plan='## Intent\nx.').status_code == 400        # a plan needs its items
    assert add_as_supervisor(app, sup_token, project='nope').status_code == 404
    assert add_as_supervisor(app, team['token']['viewer']).status_code == 403
    assert len(conn.query('SELECT * FROM project_task')) == before and rows(conn, 'supervisor.task.add') == []


def test_a_plan_the_supervisor_added_is_decided_by_a_person_in_manual_mode(app, team, conn, sup_token, member):
    manual_project(conn, team)
    resp = add_as_supervisor(app, sup_token, plan='## Intent\nTest less.', items='- [ ] **T1 Do it.** x\n  *Acceptance:* y')
    assert resp.status_code == 201
    n = resp.get_json()['task']['number']
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'plan_review'
    got = send(app, sup_token, n, 'approve', reasons=['passes the policy'])
    assert got.status_code == 200
    assert got.get_json()['decision'] == 'escalate' and got.get_json()['converted'] is True
    again = send(app, sup_token, n, 'changes', reasons=['try again']).get_json()      # decided already: a no-op
    assert again['already'] is True and again['decision'] == 'escalate'
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'plan_review'
    assert rows(conn, 'supervisor.plan.approve') == [] and len(rows(conn, 'supervisor.plan.escalate')) == 1
    assert decide(team, n, 'approve').status_code == 302                    # the assignee, a person, approves it
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'approved'


def runner_action(app, token, runner_id, action, project='alpha'):
    return new_client(app).post('/api/v1/supervisor/runners/%s/%s?project=%s' % (runner_id, action, project),
                                headers={'Authorization': 'Bearer ' + token})


@pytest.fixture
def a_runner(app, team, conn):
    from .test_runners import LABEL, beat
    beat(app, team)
    return runners.get(conn, team['alpha']['id'], LABEL)


def test_supervisor_pauses_resumes_and_wakes_a_runner(app, team, conn, sup_token, a_runner):
    from .test_runners import beat
    rid = a_runner['id']
    resp = runner_action(app, sup_token, rid, 'pause')
    assert resp.status_code == 200 and resp.get_json()['changed'] and resp.get_json()['runner']['control'] == 'pause'
    assert runner_action(app, sup_token, rid, 'pause').get_json()['changed'] is False      # repeating changes nothing
    assert beat(app, team).get_json()['control'] == 'pause'
    assert runner_action(app, sup_token, rid, 'wake').status_code == 409                   # a pause is not overridden
    assert runner_action(app, sup_token, rid, 'resume').get_json()['runner']['control'] is None
    assert runner_action(app, sup_token, rid, 'resume').status_code == 409                 # nothing left to resume
    assert runner_action(app, sup_token, rid, 'wake').get_json()['runner']['control'] == 'wake'
    assert beat(app, team).get_json()['control'] == 'wake' and beat(app, team).get_json()['control'] is None
    assert [r['action'] for r in audit.rows(conn) if r['action'].startswith('supervisor.runner.')] == [
        'supervisor.runner.pause', 'supervisor.runner.resume', 'supervisor.runner.wake']


def test_supervisor_cannot_resume_a_persons_pause(app, team, conn, sup_token, a_runner):
    from .test_runner_pages import control_url, post
    assert post(team['clients']['dev'], control_url(a_runner, 'pause'), confirm='1').status_code == 302
    assert runner_action(app, sup_token, a_runner['id'], 'resume').status_code == 403
    assert runners.get(conn, team['alpha']['id'], a_runner['label'])['control'] == 'pause'
    assert runner_action(app, sup_token, a_runner['id'], 'wake').status_code == 409
    assert audit.rows(conn, 'supervisor.runner.resume') == []


def test_runner_control_is_for_the_supervisor_or_a_developer_and_above(app, team, conn, sup_token, a_runner):
    rid = a_runner['id']
    assert runner_action(app, team['token']['viewer'], rid, 'pause').status_code == 403
    assert new_client(app).post('/api/v1/supervisor/runners/%s/pause?project=alpha' % rid).status_code == 401
    assert runner_action(app, sup_token, rid, 'stop').status_code == 404
    assert runner_action(app, sup_token, rid, 'nope').status_code == 404
    assert runner_action(app, sup_token, 'nope', 'pause').status_code == 404
    assert runner_action(app, sup_token, rid, 'pause', project='nope').status_code == 404
    assert runners.get(conn, team['alpha']['id'], a_runner['label'])['control'] is None


def test_a_supervisor_task_can_carry_priority_and_dependencies(app, team, conn, sup_token):
    first = add_as_supervisor(app, sup_token, title='First').get_json()['task']['number']
    resp = add_as_supervisor(app, sup_token, title='Second', priority=1, depends_on=[first])
    assert resp.status_code == 201
    row = tasks.get(conn, team['alpha']['id'], resp.get_json()['task']['number'])
    assert row['priority'] == 1 and tasks.depends_of(row) == [first]
    assert add_as_supervisor(app, sup_token, title='Bad', depends_on=[999]).status_code == 400
