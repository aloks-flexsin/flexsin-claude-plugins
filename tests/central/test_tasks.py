"""Central service: tasks, claims and plan approval (decision record 0007, step 4)."""
import datetime
import json

import pytest

from central import accounts, audit, personal, projects, tasks, tokens

from .test_access import PW, new_client, post, signed_in
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)

START = datetime.datetime(2026, 11, 23, 9, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def clock(app):
    class Clock:
        now = START

        def __call__(self):
            return self.now
    c = Clock()
    app.config['CLOCK'] = c
    return c


@pytest.fixture
def team(app, world, conn, clock):
    """alpha with a developer (dev) and the world's owner/manager/viewer/admin; personal tokens for dev and viewer."""
    accounts.create(conn, 'dev@example.com', PW)
    projects.add_member(conn, world['alpha']['id'], 'dev@example.com', 'developer', AT)
    dev = accounts.find_by_email(conn, 'dev@example.com')
    world['accounts']['dev'] = dev
    world['clients']['dev'] = signed_in(app, 'dev@example.com')
    world['token'] = {who: personal.create_token(conn, world['accounts'][who]['id'], who, START)
                      for who in ('dev', 'viewer', 'manager')}
    return world


def api(app, method, path, token, body=None):
    c = new_client(app)
    sep = '&' if '?' in path else '?'
    return c.open(path + sep + 'project=alpha', method=method, data=json.dumps(body or {}),
                  headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})


def add(team, title='CSV export', who='dev', **form):
    resp = post(team['clients'][who], '/projects/alpha/tasks', title=title, description='Export the table as CSV.', **form)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return int(resp.headers['Location'].rsplit('/', 1)[1])


def take(app, team, runner='alok/bhawish-claude-auto-runner', who='dev'):
    return api(app, 'POST', '/api/v1/tasks/next', team['token'][who], {'runner': runner}).get_json()


def plan(app, team, number, claim, who='dev'):
    return api(app, 'POST', '/api/v1/tasks/%d/plan' % number, team['token'][who],
               {'claim': claim, 'plan': '## Intent\nExport CSV.', 'items': '- [ ] **E1 Export.** ...'})


def decide(team, number, decision, who='dev', comment=''):
    return post(team['clients'][who], '/projects/alpha/tasks/%d/decide' % number, decision=decision, comment=comment,
                confirm='1')


# ---------- adding and seeing ----------

def test_people_add_tasks_for_themselves_and_everyone_sees_them(team, conn):
    n = add(team)
    row = tasks.get(conn, team['alpha']['id'], n)
    assert (row['status'], row['assignee_email'], row['number']) == ('open', 'dev@example.com', 1)
    page = team['clients']['viewer'].get('/projects/alpha/tasks').get_data(as_text=True)
    assert 'CSV export' in page and 'dev@example.com' in page and 'Add a task' not in page   # viewers cannot run runners
    assert post(team['clients']['viewer'], '/projects/alpha/tasks', title='x').status_code == 400
    assert len(audit.rows(conn, 'task.create')) == 1


def test_only_admins_add_for_someone_else_and_only_for_runners(team, conn):
    dev_id, viewer_id = team['accounts']['dev']['id'], team['accounts']['viewer']['id']
    assert post(team['clients']['manager'], '/projects/alpha/tasks', title='x', assignee=dev_id).status_code == 403
    n = add(team, who='admin', assignee=dev_id)
    assert tasks.get(conn, team['alpha']['id'], n)['assignee_email'] == 'dev@example.com'
    assert post(team['clients']['admin'], '/projects/alpha/tasks', title='x', assignee=viewer_id).status_code == 400


# ---------- claims and plans ----------

def test_the_full_life_of_a_task(app, team, conn, clock):
    n = add(team)
    got = take(app, team)
    assert got['action'] == 'plan' and got['task']['number'] == n and got['task']['description'] == 'Export the table as CSV.'
    claim = got['claim']
    assert plan(app, team, n, claim).status_code == 200
    page = team['clients']['dev'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'Export CSV.' in page and 'The plan waits for your decision.' in page
    assert '<code>E1</code> <strong>Export</strong>' in page and 'built-in method' not in page   # the items, not dict.items
    assert take(app, team)['task'] is None                          # nothing else to do while the plan waits
    clock.now += datetime.timedelta(hours=5)                         # waiting for a person never expires a claim
    assert tasks.claim_live(tasks.get(conn, team['alpha']['id'], n), clock.now)
    team['clients']['dev'] = signed_in(app, 'dev@example.com')      # the browser's 2-hour idle session ended meanwhile
    assert decide(team, n, 'approve').status_code == 302
    got = take(app, team)
    assert got['action'] == 'build' and got['task']['items'].startswith('- [ ] **E1')
    claim = got['claim']
    assert api(app, 'POST', '/api/v1/tasks/%d/heartbeat' % n, team['token']['dev'], {'claim': claim}).status_code == 200
    resp = api(app, 'POST', '/api/v1/tasks/%d/report' % n, team['token']['dev'],
               {'claim': claim, 'result': 'done', 'branch': 'alok/bhawish-claude-auto-runner/feat/csv-export',
                'pr_url': 'https://github.com/o/r/pull/3'})
    assert resp.get_json()['task']['status'] == 'done'
    row = tasks.get(conn, team['alpha']['id'], n)
    assert row['pr_url'] == 'https://github.com/o/r/pull/3' and row['claim_id'] is None


def test_request_changes_sends_it_back_with_the_comment(app, team, conn):
    n = add(team)
    plan(app, team, n, take(app, team)['claim'])
    assert decide(team, n, 'changes').status_code == 400                       # say what should change
    assert decide(team, n, 'changes', who='manager', comment='Use semicolons.').status_code == 302
    got = take(app, team)
    assert got['action'] == 'plan' and got['task']['comment'] == 'Use semicolons.'
    assert plan(app, team, n, got['claim']).status_code == 200


def test_reject_closes_it(app, team, conn):
    n = add(team)
    plan(app, team, n, take(app, team)['claim'])
    decide(team, n, 'reject', who='owner')
    row = tasks.get(conn, team['alpha']['id'], n)
    assert row['status'] == 'rejected' and row['claim_id'] is None and take(app, team)['task'] is None


def test_viewers_and_strangers_do_not_decide(app, team):
    n = add(team)
    plan(app, team, n, take(app, team)['claim'])
    assert decide(team, n, 'approve', who='viewer').status_code == 403


def test_a_second_runner_of_the_same_person_skips_a_live_claim(app, team, conn, clock):
    n = add(team)
    first = take(app, team, runner='laptop')
    assert take(app, team, runner='desktop')['task'] is None
    clock.now += tasks.CLAIM_TTL                                         # the laptop went quiet
    second = take(app, team, runner='desktop')
    assert second['task']['number'] == n and second['claim'] != first['claim']
    resp = plan(app, team, n, first['claim'])
    assert resp.status_code == 409 and 'stop working on it' in resp.get_json()['error']


def test_an_admin_releases_and_reassigns(app, team, conn):
    n = add(team)
    claim = take(app, team)['claim']
    assert post(team['clients']['manager'], '/projects/alpha/tasks/%d/release' % n, confirm='1').status_code == 404   # admins only
    assert post(team['clients']['admin'], '/projects/alpha/tasks/%d/release' % n, confirm='1',
                assignee=team['accounts']['manager']['id']).status_code == 302
    row = tasks.get(conn, team['alpha']['id'], n)
    assert row['assignee_email'] == 'manager@example.com' and row['status'] == 'open' and row['claim_id'] is None
    assert api(app, 'POST', '/api/v1/tasks/%d/heartbeat' % n, team['token']['dev'], {'claim': claim}).status_code == 409
    assert take(app, team, who='manager')['task']['number'] == n
    assert len(audit.rows(conn, 'task.reassign')) == 1


def record(app, team, runner='laptop'):
    resp = api(app, 'POST', '/api/v1/tasks', team['token']['dev'],
               {'runner': runner, 'title': 'Fix login', 'plan': 'p', 'items': '- [ ] **F1 Fix.**'})
    assert resp.status_code == 200
    return resp.get_json()


def beat(app, team, number, claim, **extra):
    return api(app, 'POST', '/api/v1/tasks/%d/heartbeat' % number, team['token']['dev'], dict(claim=claim, **extra))


def test_a_terminal_task_is_recorded_as_queued_and_its_first_heartbeat_starts_it(app, team, conn):
    body = record(app, team)
    n = body['task']['number']
    assert body['task']['status'] == 'queued' and body['claim']
    resp = beat(app, team, n, body['claim'])
    assert resp.status_code == 200 and resp.get_json()['task']['status'] == 'building'


def test_a_queued_claim_outlives_the_ttl_but_a_building_one_expires(app, team, conn, clock):
    body = record(app, team)
    n = body['task']['number']
    clock.now += datetime.timedelta(hours=3)
    assert tasks.claim_live(tasks.get(conn, team['alpha']['id'], n), clock.now)
    assert beat(app, team, n, body['claim'], state='queued').status_code == 200      # still held
    assert beat(app, team, n, body['claim'], state='building').get_json()['task']['status'] == 'building'
    clock.now += datetime.timedelta(hours=3)
    assert beat(app, team, n, body['claim']).status_code == 409


def test_state_queued_moves_an_approved_task_back_and_other_runners_skip_it(app, team, conn):
    n = add(team)
    claim = take(app, team, runner='laptop')['claim']
    plan(app, team, n, claim)
    decide(team, n, 'approve')
    claim = take(app, team, runner='laptop')['claim']                     # approved -> building
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'building'
    assert beat(app, team, n, claim, state='queued').get_json()['task']['status'] == 'queued'
    assert beat(app, team, n, claim, state='nonsense').status_code == 400
    assert take(app, team, runner='desktop')['task'] is None


def test_report_on_a_queued_task_ends_it_as_done(app, team, conn):
    body = record(app, team)
    resp = api(app, 'POST', '/api/v1/tasks/%d/report' % body['task']['number'], team['token']['dev'],
               {'claim': body['claim'], 'result': 'done'})
    assert resp.status_code == 200 and resp.get_json()['task']['status'] == 'done'


def test_admin_release_moves_a_queued_task_to_approved_and_next_returns_it(app, team, conn):
    body = record(app, team)
    n = body['task']['number']
    assert post(team['clients']['admin'], '/projects/alpha/tasks/%d/release' % n, confirm='1').status_code == 302
    row = tasks.get(conn, team['alpha']['id'], n)
    assert row['status'] == 'approved' and row['claim_id'] is None
    assert take(app, team)['task']['number'] == n


def test_the_tasks_page_lists_a_queued_task_under_in_progress(app, team, conn):
    n = record(app, team)['task']['number']
    page = team['clients']['dev'].get('/projects/alpha/tasks?state=progress').get_data(as_text=True)
    assert 'Fix login' in page and 'Queued' in page
    assert 'Queued' in team['clients']['dev'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)


def test_task_claims_record_the_claude_identity(app, team, conn):
    from central import identities
    dev_id = team['accounts']['dev']['id']
    add(team)
    take(app, team, runner='alok/bhawish-claude-auto-runner')
    [row] = identities.of_account(conn, dev_id)
    assert (row['username'], row['claude_account']) == ('alok', 'bhawish')
    assert 'bhawish' in team['clients']['dev'].get('/account').get_data(as_text=True)
    assert any(r['account_id'] == dev_id for r in identities.of_project(conn, team['alpha']['id']))


def test_terminal_tasks_and_plans_record_the_identity_and_odd_runners_record_nothing(app, team, conn):
    from central import identities
    dev_id = team['accounts']['dev']['id']
    resp = api(app, 'POST', '/api/v1/tasks', team['token']['dev'],
               {'runner': 'sam/work-claude-auto-runner', 'title': 'Fix', 'plan': 'p', 'items': '- [ ] **F1 Fix.**'})
    assert resp.status_code == 200
    n = add(team, title='Other')
    claim = take(app, team, runner='laptop')
    assert [(r['username'], r['claude_account']) for r in identities.of_account(conn, dev_id)] == [('sam', 'work')]
    api(app, 'POST', '/api/v1/tasks/%d/plan' % claim['task']['number'], team['token']['dev'],
        {'claim': claim['claim'], 'plan': 'p', 'items': '- [ ] **E1 x.**', 'runner': 'kim/third-claude-auto-runner'})
    assert [r['username'] for r in identities.of_account(conn, dev_id)] == ['kim', 'sam']
    assert n


def test_the_task_api_needs_a_personal_token_of_a_runner(app, team, conn):
    raw, _ = tokens.create(conn, team['alpha']['id'], ['pull', 'ingest'], None, AT)
    assert api(app, 'POST', '/api/v1/tasks/next', raw, {'runner': 'ci'}).status_code == 403
    assert api(app, 'POST', '/api/v1/tasks/next', team['token']['viewer'], {'runner': 'x'}).status_code == 403
    assert api(app, 'GET', '/api/v1/tasks', team['token']['dev']).status_code == 200


def test_everyone_sees_what_the_others_are_doing_through_the_api(app, team, conn):
    add(team)
    take(app, team, runner='alok/bhawish-claude-auto-runner')
    [t] = api(app, 'GET', '/api/v1/tasks', team['token']['manager']).get_json()['tasks']
    assert t['runner'] == 'alok/bhawish-claude-auto-runner' and t['assignee'] == 'dev@example.com' and 'claim' not in t


def test_a_queued_task_keeps_a_waiting_note_until_it_starts_building(app, team, conn):
    body = record(app, team)
    n, note = body['task']['number'], 'waits: alok/x-claude-auto-runner/feat/a is not merged into origin/main yet'
    assert beat(app, team, n, body['claim'], state='queued', note=note).get_json()['task']['status'] == 'queued'
    assert tasks.get(conn, team['alpha']['id'], n)['note'] == note
    page = team['clients']['dev'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'waits' in page and 'feat/a' in page
    assert beat(app, team, n, body['claim'], state='queued').status_code == 200             # no note: kept
    assert tasks.get(conn, team['alpha']['id'], n)['note'] == note
    assert beat(app, team, n, body['claim'], state='building').get_json()['task']['status'] == 'building'
    assert not tasks.get(conn, team['alpha']['id'], n)['note']


def test_a_waiting_note_is_limited_to_300_characters(app, team, conn):
    body = record(app, team)
    n = body['task']['number']
    assert beat(app, team, n, body['claim'], state='queued', note='x' * 301).status_code == 400
    assert beat(app, team, n, body['claim'], state='queued', note='x' * 300).status_code == 200


def test_release_clears_a_waiting_note(app, team, conn):
    body = record(app, team)
    n = body['task']['number']
    beat(app, team, n, body['claim'], state='queued', note='waits: b is not merged into origin/main yet')
    assert post(team['clients']['admin'], '/projects/alpha/tasks/%d/release' % n, confirm='1').status_code == 302
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'approved'
    assert not tasks.get(conn, team['alpha']['id'], n)['note']


# ---------- closing a task from its page ----------

def close(team, number, who='dev', reason='Not needed any more.', **form):
    return post(team['clients'][who], '/projects/alpha/tasks/%d/close' % number, reason=reason, **form)


def test_the_assignee_and_an_admin_close_an_open_task_with_a_reason(app, team, conn):
    n, m = add(team), add(team, title='Other')
    assert 'Close task' in close(team, n).get_data(as_text=True)                 # asks first
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'open'
    assert close(team, n, confirm='1').status_code == 302
    row = tasks.get(conn, team['alpha']['id'], n)
    assert (row['status'], row['note'], row['claim_id']) == ('rejected', 'Not needed any more.', None)
    assert close(team, m, who='admin', reason='Duplicate.', confirm='1').status_code == 302
    assert tasks.get(conn, team['alpha']['id'], m)['status'] == 'rejected'
    assert take(app, team)['task'] is None                                       # next no longer takes it
    assert len(audit.rows(conn, 'task.close')) == 2
    page = team['clients']['viewer'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'closed this task: Not needed any more.' in page and 'Dev closed' in page


def test_close_is_for_deciders_and_shows_its_form_only_to_them(app, team, conn):
    n = add(team, who='manager')
    assert close(team, n, who='dev', confirm='1').status_code == 403            # a developer who is not the assignee
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'open'
    assert close(team, n, who='dev').status_code == 403                          # refused before any confirm step
    assert 'id="close-reason"' not in team['clients']['dev'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'id="close-reason"' in team['clients']['manager'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert close(team, n, who='manager', confirm='1').status_code == 302


def test_a_building_or_final_task_is_not_closed(app, team, conn):
    n = add(team)
    plan(app, team, n, take(app, team)['claim'])
    decide(team, n, 'approve')
    assert take(app, team)['action'] == 'build'
    resp = close(team, n, confirm='1')
    assert resp.status_code == 409 and 'stop its runner first' in resp.get_data(as_text=True)
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'building'
    assert 'id="close-reason"' not in team['clients']['dev'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    done = add(team, title='Done one')
    assert close(team, done, confirm='1').status_code == 302
    assert close(team, done, confirm='1').status_code == 409                     # already closed
    assert len(audit.rows(conn, 'task.close')) == 1


def test_close_needs_a_reason_and_csrf(app, team, conn):
    n = add(team)
    assert close(team, n, reason='  ', confirm='1').status_code == 400
    assert close(team, n, reason='x' * 501, confirm='1').status_code == 400
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'open'
    resp = team['clients']['dev'].post('/projects/alpha/tasks/%d/close' % n, data={'reason': 'x', 'confirm': '1'})
    assert resp.status_code == 400 and tasks.get(conn, team['alpha']['id'], n)['status'] == 'open'
