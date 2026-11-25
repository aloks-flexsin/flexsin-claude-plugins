"""Central service: deciding on a waiting plan through the API (`POST /api/v1/tasks/<N>/decide`)."""
import json

from central import audit, tasks, tokens

from .test_tasks import add, api, clock, plan, take, team  # noqa: F401  (fixtures)
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)


def waiting(app, team):
    n = add(team)
    assert plan(app, team, n, take(app, team)['claim']).status_code == 200
    return n


def decide_api(app, team, n, decision, who='dev', comment=None, token=None):
    body = {'decision': decision}
    if comment is not None:
        body['comment'] = comment
    return api(app, 'POST', '/api/v1/tasks/%d/decide' % n, token or team['token'][who], body)


def test_the_assignee_approves_through_the_api(app, team, conn):
    n = waiting(app, team)
    resp = decide_api(app, team, n, 'approve')
    assert resp.status_code == 200 and resp.get_json()['task']['status'] == 'approved'
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'approved'
    [row] = audit.rows(conn, 'task.plan.approve')
    detail = json.loads(row['detail']) if isinstance(row['detail'], str) else row['detail']
    assert detail == {'number': n, 'via': 'api'}
    page = team['clients']['dev'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'approved the plan for this task (through the API)' in page
    assert take(app, team)['action'] == 'build'


def test_a_viewer_is_refused(app, team, conn):
    n = waiting(app, team)
    assert decide_api(app, team, n, 'approve', who='viewer').status_code == 403
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'plan_review'


def test_a_task_without_a_waiting_plan_is_409(app, team):
    n = add(team)
    assert decide_api(app, team, n, 'approve').status_code == 409


def test_changes_needs_a_comment(app, team, conn):
    n = waiting(app, team)
    assert decide_api(app, team, n, 'changes').status_code == 400
    resp = decide_api(app, team, n, 'changes', who='manager', comment='Use semicolons.')
    assert resp.status_code == 200 and resp.get_json()['task']['status'] == 'changes_requested'
    assert tasks.get(conn, team['alpha']['id'], n)['decision_comment'] == 'Use semicolons.'


def test_an_unknown_decision_is_400(app, team, conn):
    n = waiting(app, team)
    assert decide_api(app, team, n, 'maybe').status_code == 400
    assert audit.rows(conn, 'task.plan.maybe') == []


def test_reject_ends_the_claim(app, team, conn):
    n = waiting(app, team)
    resp = decide_api(app, team, n, 'reject')
    assert resp.status_code == 200
    row = tasks.get(conn, team['alpha']['id'], n)
    assert row['status'] == 'rejected' and row['claim_id'] is None


def test_a_project_token_is_refused(app, team, conn):
    n = waiting(app, team)
    raw, _ = tokens.create(conn, team['alpha']['id'], ['pull', 'ingest'], None, AT)
    assert decide_api(app, team, n, 'approve', token=raw).status_code == 403
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'plan_review'


def test_an_unknown_task_is_404(app, team):
    assert decide_api(app, team, 999, 'approve').status_code == 404
