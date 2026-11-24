"""Central service: a plan proposed from the terminal (`"approval": "page"`) waits for approval on the task page."""
from central import tasks

from .test_tasks import api, clock, decide, take, team  # noqa: F401  (fixtures)
from .test_repo_url import conn, world  # noqa: F401  (fixtures)

PROPOSAL = {'runner': 'alok/bhawish-claude-cli', 'title': 'Fix login', 'plan': '## Intent\nFix it.',
            'items': '- [ ] **F1 Fix.** ...', 'approval': 'page'}


def test_a_proposed_plan_is_stored_for_review_and_can_be_approved(app, team, conn, clock):
    resp = api(app, 'POST', '/api/v1/tasks', team['token']['dev'], PROPOSAL)
    body = resp.get_json()
    assert resp.status_code == 200 and body['task']['status'] == 'plan_review' and body['claim']
    n = body['task']['number']
    row = tasks.get(conn, team['alpha']['id'], n)
    assert row['plan'] == '## Intent\nFix it.' and row['items'] == '- [ ] **F1 Fix.** ...'
    assert row['claim_runner'] == 'alok/bhawish-claude-cli' and tasks.claim_live(row, clock.now + tasks.CLAIM_TTL * 3)
    page = team['clients']['dev'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'The plan waits for your decision.' in page
    assert take(app, team)['task'] is None                         # nothing to build until a person approves
    assert decide(team, n, 'approve').status_code == 302
    got = take(app, team, runner='alok/bhawish-claude-cli')
    assert got['action'] == 'build' and got['task']['number'] == n and got['task']['status'] == 'building'


def test_the_default_is_still_not_waiting_for_the_page(app, team):
    body = api(app, 'POST', '/api/v1/tasks', team['token']['dev'], {**PROPOSAL, 'approval': None}).get_json()
    assert body['task']['status'] == 'queued'
    body = api(app, 'POST', '/api/v1/tasks', team['token']['dev'], {**PROPOSAL, 'approval': 'terminal'}).get_json()
    assert body['task']['status'] == 'queued'


def test_a_proposal_needs_a_plan_and_items_and_a_known_approval(app, team):
    assert api(app, 'POST', '/api/v1/tasks', team['token']['dev'], {**PROPOSAL, 'items': ''}).status_code == 400
    assert api(app, 'POST', '/api/v1/tasks', team['token']['dev'], {**PROPOSAL, 'plan': ' '}).status_code == 400
    assert api(app, 'POST', '/api/v1/tasks', team['token']['dev'], {**PROPOSAL, 'approval': 'chat'}).status_code == 400
