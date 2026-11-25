"""The Supervisor's plan step (bin/autosupervisor.py) driven through the real Flask test client: journeys J1 and J2."""
import json

import pytest

from central import accounts, audit, personal, projects, tasks
from tests.auto_runner.test_runner import ar

from .test_account_admin import new_client
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)
from .test_tasks import PW, START, add, api, clock, take, team  # noqa: F401  (fixtures)

sup_mod = ar._load('autorunner_supervisor', 'autosupervisor.py')

ITEMS = "- [ ] **X1 Add thing.** Add auto-runner/bin/thing.py.\n  *Acceptance:* tests/test_thing.py passes.\n  *Slice:* 1 Thing\n"
GOOD = "# Plan\n\n## Queue items\n" + ITEMS + "\n## Needs a person\nNone.\n"
BAD = GOOD.replace('auto-runner/bin/thing.py', 'migrations/0001.sql')
POLICY = json.dumps(dict(base='main', restricted_paths=['migrations/**', '.github/']))


@pytest.fixture
def sup(conn, team):
    account = accounts.create(conn, 'sup@example.com', PW, role='supervisor')
    projects.add_member(conn, team['alpha']['id'], 'sup@example.com', 'manager', START)
    return personal.create_token(conn, account['id'], 'sup', START)


def http_for(app, token):
    calls = []

    def http(method, path, body=None):
        calls.append((method, path))
        resp = new_client(app).open(path, method=method, json=body, headers={'Authorization': 'Bearer ' + token})
        return resp.status_code, resp.get_json()
    http.calls = calls
    return http


def reader(text=POLICY, sha='sha-1'):
    return lambda ref, path: None if text is None else (text, sha)


def post_plan(app, team, n, text, runner='alok/bhawish-claude-auto-runner'):
    claim = take(app, team, runner=runner)['claim']
    resp = api(app, 'POST', '/api/v1/tasks/%d/plan' % n, team['token']['dev'], {'claim': claim, 'plan': text, 'items': ITEMS})
    assert resp.status_code == 200
    return claim


def status(conn, team, n):
    return tasks.get(conn, team['alpha']['id'], n)['status']


def test_plan_approved_or_sent_back(app, team, conn, sup):
    http = http_for(app, sup)
    # J1: a passing plan is approved, by the supervisor account, with the policy SHA in the audit row
    n = add(team)
    post_plan(app, team, n, GOOD)
    [done] = sup_mod.plan_step('alpha', http, reader(), 'origin/main')
    assert (done['task'], done['verdict'], done['decision']) == (n, 'approve', 'approve')
    assert status(conn, team, n) == 'approved'
    [row] = audit.rows(conn, 'supervisor.plan.approve')
    assert row['detail']['policy_sha'] == 'sha-1'
    assert tasks.get(conn, team['alpha']['id'], n)['decided_by'] == accounts.find_by_email(conn, 'sup@example.com')['id']
    # J2: a plan naming a restricted path is sent back twice, then left for a manager
    m = add(team, title='Second')
    runner = 'alok/second-claude-auto-runner'
    post_plan(app, team, m, BAD, runner)
    [sent] = sup_mod.plan_step('alpha', http, reader(), 'origin/main')
    assert sent['decision'] == 'changes' and status(conn, team, m) == 'changes_requested'
    assert 'migrations/0001.sql' in tasks.get(conn, team['alpha']['id'], m)['decision_comment']
    assert sup_mod.plan_step('alpha', http, reader(), 'origin/main') == []                    # nothing waits
    post_plan(app, team, m, BAD, runner)
    assert sup_mod.plan_step('alpha', http, reader(), 'origin/main')[0]['decision'] == 'changes'
    post_plan(app, team, m, BAD, runner)
    [third] = sup_mod.plan_step('alpha', http, reader(), 'origin/main')
    assert third['verdict'] == 'changes' and third['decision'] == 'escalate'
    assert status(conn, team, m) == 'plan_review'
    assert len(audit.rows(conn, 'supervisor.plan.changes')) == 2 and len(audit.rows(conn, 'supervisor.plan.escalate')) == 1
    page = team['clients']['manager'].get('/projects/alpha/tasks?state=approval').get_data(as_text=True)
    assert 'Second' in page
    posts = len(http.calls)
    assert sup_mod.plan_step('alpha', http, reader(), 'origin/main') == []                    # a further pass does nothing
    assert [c[0] for c in http.calls[posts:]] == ['GET'] and len(audit.rows(conn, 'supervisor.plan.escalate')) == 1


def test_a_missing_policy_leaves_the_plan_for_a_person_once(app, team, conn, sup):
    http = http_for(app, sup)
    n = add(team)
    post_plan(app, team, n, GOOD)
    [done] = sup_mod.plan_step('alpha', http, reader(None), 'origin/main')
    assert done['verdict'] == 'person' and done['decision'] == 'escalate' and status(conn, team, n) == 'plan_review'
    assert sup_mod.plan_step('alpha', http, reader(None), 'origin/main') == []
    [row] = audit.rows(conn, 'supervisor.plan.escalate')
    assert row['detail']['policy_sha'] is None and any('missing' in r for r in row['detail']['reasons'])


def test_an_unreadable_state_is_reported_and_decides_nothing(app, team, conn, sup):
    out = sup_mod.plan_step('alpha', lambda m, p, b=None: (403, {'error': 'no'}), reader(), 'origin/main')
    assert out[0]['task'] is None and out[0]['status'] == 403
    assert audit.rows(conn, 'supervisor.plan.approve') == []
