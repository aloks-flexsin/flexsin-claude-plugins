"""Central service: the project autonomy switch and the per-task manual flag (decision record 0012, Phase 3b).
Auto mode (the default): the supervisor decides without a manager membership, also on tasks it added itself, and
approves the latest plan instead of escalating after PLAN_ROUND_CAP rounds. A task marked manual is left to a person."""
import json

from central import audit, projects, supervisor, tasks

from .test_supervisor_api import add_as_supervisor, get, manual_project, rows, send, sup_token, waiting  # noqa: F401
from .test_tasks import PW, START, add, clock, plan, take, team  # noqa: F401  (fixtures)
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)
from .test_access import post, signed_in


def row_of(conn, team, n):
    return tasks.get(conn, team['alpha']['id'], n)


# ---------- the switch and the flag ----------

def test_projects_default_to_auto_and_tasks_to_not_manual(conn, team):
    assert projects.autonomy(team['alpha']) == 'auto'
    assert projects.autonomy({}) == 'auto' and projects.autonomy({'autonomy': 'odd'}) == 'auto'
    n = add(team)
    assert tasks.manual(row_of(conn, team, n)) is False and tasks.auto_mode(team['alpha'], row_of(conn, team, n))


def test_the_columns_are_migrated(conn):
    assert 'autonomy' in conn.columns('project') and 'manual' in conn.columns('project_task')


def test_an_owner_switches_the_project_and_it_is_audited(app, conn, team):
    client = signed_in(app, 'owner@example.com')
    page = client.get('/projects/alpha/settings/repository').get_data(as_text=True)
    assert 'Autonomy' in page and 'Switch to manual' in page
    assert post(client, '/projects/alpha/autonomy', autonomy='manual').status_code in (302, 303)
    assert projects.autonomy(projects.get_by_slug(conn, 'alpha')) == 'manual'
    [row] = conn.query("SELECT * FROM audit WHERE action = 'project.autonomy'")
    assert json.loads(row['detail']) == {'from': 'auto', 'to': 'manual'}
    assert post(client, '/projects/alpha/autonomy', autonomy='sometimes').status_code == 400
    assert 'Switch to auto' in client.get('/projects/alpha/settings/repository').get_data(as_text=True)


def test_a_developer_cannot_switch_the_project(app, conn, team):
    client = signed_in(app, 'dev@example.com')
    assert 'Switch to manual' not in client.get('/projects/alpha/settings/repository').get_data(as_text=True)
    assert post(client, '/projects/alpha/autonomy', autonomy='manual').status_code in (403, 404)
    assert projects.autonomy(projects.get_by_slug(conn, 'alpha')) == 'auto'


def test_the_assignee_marks_a_task_manual_and_it_is_audited(app, conn, team):
    n = add(team)
    client = signed_in(app, 'dev@example.com')
    page = client.get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'Mark manual: a person decides' in page and 'The supervisor (auto mode)' in page
    assert post(client, '/projects/alpha/tasks/%d/manual' % n, manual='1').status_code in (302, 303)
    assert tasks.manual(row_of(conn, team, n)) is True
    [row] = conn.query("SELECT * FROM audit WHERE action = 'task.manual'")
    assert json.loads(row['detail']) == {'number': n, 'manual': True, 'before': False}
    page = client.get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'Let the supervisor decide' in page and 'this task is manual' in page
    assert post(client, '/projects/alpha/tasks/%d/manual' % n, manual='x').status_code == 400


def test_a_viewer_cannot_mark_a_task_manual(app, conn, team):
    n = add(team)
    assert post(signed_in(app, 'viewer@example.com'), '/projects/alpha/tasks/%d/manual' % n, manual='1').status_code == 404
    assert tasks.manual(row_of(conn, team, n)) is False


def test_the_state_and_the_api_show_the_mode(app, conn, team, sup_token):
    n = add(team)
    tasks.set_manual(conn, row_of(conn, team, n), True)
    conn.commit()
    state = get(app, '/api/v1/supervisor/state?project=alpha', sup_token).get_json()
    assert state['autonomy'] == 'auto' and state['tasks'][0]['manual'] is True
    manual_project(conn, team)
    assert get(app, '/api/v1/supervisor/state?project=alpha', sup_token).get_json()['autonomy'] == 'manual'


# ---------- plan decisions in auto mode ----------

def test_in_auto_mode_a_non_member_supervisor_approves(app, conn, team, sup_token):
    n = waiting(app, team)
    resp = send(app, sup_token, n, 'approve', reasons=['passes the policy'])
    assert resp.status_code == 200 and resp.get_json()['decision'] == 'approve'
    assert row_of(conn, team, n)['status'] == 'approved' and len(rows(conn, 'supervisor.plan.approve')) == 1


def test_in_auto_mode_the_third_round_approves_the_latest_plan(app, conn, team, sup_token):
    n = add(team)
    for _ in (1, 2):
        assert plan(app, team, n, take(app, team)['claim']).status_code == 200
        assert send(app, sup_token, n, 'changes', reasons=['restricted path']).get_json()['decision'] == 'changes'
    assert plan(app, team, n, take(app, team)['claim']).status_code == 200
    resp = send(app, sup_token, n, 'changes', reasons=['restricted path']).get_json()
    assert resp['decision'] == 'approve' and resp['converted'] is True
    row = row_of(conn, team, n)
    assert row['status'] == 'approved' and 'approves the latest plan' in row['decision_comment']
    assert rows(conn, 'supervisor.plan.escalate') == [] and len(rows(conn, 'supervisor.plan.approve')) == 1


def test_in_auto_mode_the_supervisor_decides_a_task_it_added(app, conn, team, sup_token):
    resp = add_as_supervisor(app, sup_token, plan='## Intent\nTest less.', items='- [ ] **T1 Do it.** x\n  *Acceptance:* y')
    n = resp.get_json()['task']['number']
    got = send(app, sup_token, n, 'approve', reasons=['passes the policy']).get_json()
    assert got['decision'] == 'approve' and got['converted'] is False
    assert row_of(conn, team, n)['status'] == 'approved'


def test_a_task_marked_manual_is_left_to_a_person(app, conn, team, sup_token):
    n = waiting(app, team)
    tasks.set_manual(conn, row_of(conn, team, n), True)
    conn.commit()
    got = send(app, sup_token, n, 'approve', reasons=['passes the policy']).get_json()
    assert got['decision'] == 'escalate' and got['converted'] is True
    assert row_of(conn, team, n)['status'] == 'plan_review'
    [row] = rows(conn, 'supervisor.plan.escalate')
    detail = row['detail'] if isinstance(row['detail'], dict) else json.loads(row['detail'])
    assert 'marked manual' in detail['reasons'][-1]


def test_a_task_marked_manual_still_needs_a_membership_to_decide_directly(conn, team, sup_token):
    n = add(team)
    row = row_of(conn, team, n)
    sup = conn.query_one("SELECT * FROM account WHERE email = 'sup@example.com'")
    assert tasks.can_decide(conn, team['alpha'], row, sup) is True
    tasks.set_manual(conn, row, True)
    assert tasks.can_decide(conn, team['alpha'], row_of(conn, team, n), sup) is False


def test_a_caller_whose_repository_is_manual_keeps_the_old_escalation(app, conn, team, sup_token):
    n = add(team)
    for _ in (1, 2):
        assert plan(app, team, n, take(app, team)['claim']).status_code == 200
        projects.add_member(conn, team['alpha']['id'], 'sup@example.com', 'manager', START) if _ == 1 else None
        assert send(app, sup_token, n, 'changes', reasons=['x'], autonomy='manual').get_json()['decision'] == 'changes'
    assert plan(app, team, n, take(app, team)['claim']).status_code == 200
    assert send(app, sup_token, n, 'changes', reasons=['x'], autonomy='manual').get_json()['decision'] == 'escalate'


# ---------- the Supervise stage under a member's own sign-in (decision record 0012, Phase 8) ----------

def test_a_developer_acts_as_the_supervise_stage_and_it_is_audited(app, conn, team):
    dev = team['token']['dev']
    state = get(app, '/api/v1/supervisor/state?project=alpha', dev)
    assert state.status_code == 200 and state.get_json()['autonomy'] == 'auto'
    n = waiting(app, team)
    resp = send(app, dev, n, 'approve', reasons=['passes the policy'])
    assert resp.status_code == 200 and resp.get_json()['decision'] == 'approve'
    [row] = rows(conn, 'supervisor.plan.approve')
    detail = row['detail'] if isinstance(row['detail'], dict) else json.loads(row['detail'])
    assert detail['as'] == 'supervise stage'
    added = add_as_supervisor(app, dev, assignee='manager@example.com')
    assert added.status_code == 201 and 'From: ' in added.get_json()['task']['description']


def test_a_viewer_or_an_outsider_is_refused_and_a_slug_is_not_revealed(app, conn, team):
    assert get(app, '/api/v1/supervisor/state?project=alpha', team['token']['viewer']).status_code == 403
    assert get(app, '/api/v1/supervisor/state?project=nope', team['token']['dev']).status_code == 403
    assert get(app, '/api/v1/supervisor/state?project=alpha').status_code == 401


def test_a_stage_pause_can_be_resumed_by_the_stage_but_not_a_persons(app, conn, team):
    from central import runners
    from .test_runners import LABEL, beat
    from .test_supervisor_api import runner_action
    beat(app, team)
    rid = runners.get(conn, team['alpha']['id'], LABEL)['id']
    dev = team['token']['dev']
    assert runner_action(app, dev, rid, 'pause').status_code == 200
    assert runners.by_id(conn, team['alpha']['id'], rid)['control_source'] == 'supervise'
    state = get(app, '/api/v1/supervisor/state?project=alpha', dev).get_json()
    assert state['runners'][0]['paused_by_supervisor'] is True
    assert runner_action(app, dev, rid, 'resume').status_code == 200
    row = runners.by_id(conn, team['alpha']['id'], rid)
    runners.set_control(conn, row, 'pause', team['accounts']['manager'], START)      # a person's pause
    conn.commit()
    assert runner_action(app, dev, rid, 'resume').status_code == 403
