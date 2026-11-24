"""Central service: tracking progress. A run says which task it builds, so the task page shows the build live, and the
My work page gathers, across projects, plans waiting for my approval, blockers I can answer, my tasks, what every runner
is doing and what finished."""
import datetime

from central import projects, runs

from .test_tasks import add, api, clock, decide, take, team  # noqa: F401  (fixtures)
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)

ITEMS = ('- [ ] **E1 Export link.** Add the link.\n  *Acceptance:* a test.\n'
         '- [ ] **E2 Export file.** Stream the file.\n  *Acceptance:* a test.\n')


def ev(run, type, minutes, item=None, **detail):
    t = (datetime.datetime(2026, 11, 23, 9, 0, tzinfo=datetime.timezone.utc)
         + datetime.timedelta(minutes=minutes)).isoformat()
    return {'schema': 'event.v1', 'id': '%s-%s-%s-%s' % (run, type, minutes, item), 'time': t, 'run': run,
            'project': 'alpha', 'type': type, 'item': item, 'detail': detail, 'evidence_class': 'runner_observed'}


def building_task(app, team):
    """Task 1, planned with two items, approved and taken for building by dev's runner."""
    n = add(team)
    claim = take(app, team)['claim']
    api(app, 'POST', '/api/v1/tasks/%d/plan' % n, team['token']['dev'], {'claim': claim, 'plan': 'P', 'items': ITEMS})
    decide(team, n, 'approve')
    got = take(app, team)
    assert got['action'] == 'build'
    return n, got['claim']


def send_run(app, team, n, finished=False):
    events = [ev('r1', 'run_start', 0, user='alok', account='bhawish', branch='alok/bhawish-claude-auto-runner/feat/task-1',
                 task=n),
              ev('r1', 'item_state', 1, 'E1', **{'from': 'queued', 'to': 'running'}),
              ev('r1', 'item_state', 2, 'E1', **{'from': 'running', 'to': 'done'}),
              ev('r1', 'tests', 2, 'E1', result='pass', exit=0),
              ev('r1', 'item_state', 3, 'E2', **{'from': 'queued', 'to': 'running'})]
    if finished:
        events += [ev('r1', 'item_state', 4, 'E2', **{'from': 'running', 'to': 'done'}),
                   ev('r1', 'run_end', 5, exit=0, reason='queue_empty', task=n)]
    assert api(app, 'POST', '/api/v1/events', team['token']['dev'], events).status_code == 200


def test_item_ids_and_progress():
    assert runs.item_ids(ITEMS) == ['E1', 'E2']
    r = {'items': {'E1': 'done', 'E2': 'running', 'Z9': 'done'}, 'current': 'E2'}
    p = runs.task_progress([r], ITEMS)
    assert (p['total'], p['done'], p['current']) == (2, 1, 'E2')         # only the task's own items count
    assert runs.task_progress([], ITEMS) is None


def test_the_task_page_shows_the_build_live(app, team, clock):
    n, _ = building_task(app, team)
    page = team['clients']['viewer'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'no run has sent progress yet' in page
    clock.now = datetime.datetime(2026, 11, 23, 9, 10, tzinfo=datetime.timezone.utc)
    send_run(app, team, n)
    page = team['clients']['viewer'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert '1 of 2 items' in page and 'on E2' in page and 'last tests: pass' in page
    runs_page = team['clients']['viewer'].get('/projects/alpha/runs').get_data(as_text=True)
    assert 'href="/projects/alpha/tasks/%d">#%d</a>' % (n, n) in runs_page


def test_my_work_for_the_assignee_a_manager_and_a_viewer(app, team, conn, clock):
    n = add(team)
    claim = take(app, team)['claim']
    api(app, 'POST', '/api/v1/tasks/%d/plan' % n, team['token']['dev'], {'claim': claim, 'plan': 'P', 'items': ITEMS})
    api(app, 'POST', '/api/v1/events', team['token']['dev'],
        [ev('r9', 'blocker', 0, 'E1', action='raised', id='B-1', blocker={'question': 'Which colour?'})])
    dev = team['clients']['dev'].get('/my').get_data(as_text=True)
    assert 'CSV export' in dev and 'Needs approval' in dev and '1 decision waits for you.' in dev   # mine to approve
    assert 'Which colour?' not in dev                                                    # developers do not answer blockers
    manager = team['clients']['manager'].get('/my').get_data(as_text=True)
    assert 'Waiting for you <span class="pill attention">2</span>' in manager            # the plan and the blocker
    assert 'Which colour?' in manager and 'B-1' in manager
    viewer = team['clients']['viewer'].get('/my').get_data(as_text=True)
    assert 'No plans or blockers wait for you.' in viewer
    assert 'Nothing is in progress.' in viewer                       # a plan waiting for a person is not runner work


def test_my_work_shows_progress_and_what_finished(app, team, conn, clock):
    n, claim = building_task(app, team)
    send_run(app, team, n)
    page = team['clients']['owner'].get('/my').get_data(as_text=True)
    assert '1 of 2 items' in page and 'on E2' in page
    send_run(app, team, n, finished=True)
    api(app, 'POST', '/api/v1/tasks/%d/report' % n, team['token']['dev'],
        {'claim': claim, 'result': 'done', 'pr_url': 'https://github.com/o/r/pull/7'})
    page = team['clients']['owner'].get('/my').get_data(as_text=True)
    assert 'href="https://github.com/o/r/pull/7"' in page and 'PR #7' in page and 'Nothing is in progress.' in page


def test_admins_see_every_project_and_strangers_see_nothing_of_it(app, team, conn):
    beta = projects.create(conn, 'beta', 'Beta', AT, 'https://github.com/example/beta.git')
    projects.add_member(conn, beta['id'], 'owner@example.com', 'developer', AT)
    from central import tasks
    from central import accounts
    tasks.create(conn, beta, accounts.find_by_email(conn, 'owner@example.com'),
                 accounts.find_by_email(conn, 'owner@example.com'), 'Beta work', '', datetime.datetime.now(datetime.timezone.utc))
    add(team)
    admin = team['clients']['admin'].get('/my').get_data(as_text=True)
    assert 'every project' in admin
    assert 'Beta work' not in team['clients']['viewer'].get('/my').get_data(as_text=True)
    assert team['clients']['viewer'].get('/my').status_code == 200
