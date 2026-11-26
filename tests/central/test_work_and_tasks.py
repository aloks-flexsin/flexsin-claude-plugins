"""Central service: My work, the Tasks list and the task page (UX audit, PR 3). Decisions are made where they show up
(a short plan is approved from its My work card, which returns there), the Tasks list has state tabs, "Mine only" and a
search, a task is added on its own page, and the task page shows where the task is, its items' live state and its
history."""
from central import audit, runs, tasks, ui

from .test_access import post
from .test_my_work import ITEMS, building_task, send_run
from .test_repo_url import conn, world  # noqa: F401  (fixtures)
from .test_tasks import add, api, clock, decide, take, team  # noqa: F401  (fixtures)

THREE = ITEMS + '- [ ] **E3 Docs.** Say so.\n  *Acceptance:* the guide says it.\n'


def planned(app, team, items=ITEMS, title='CSV export'):
    n = add(team, title=title)
    claim = take(app, team)['claim']
    assert api(app, 'POST', '/api/v1/tasks/%d/plan' % n, team['token']['dev'],
               {'claim': claim, 'plan': '## Intent\nExport **CSV**.', 'items': items}).status_code == 200
    return n


# ---------- helpers ----------

def test_items_parse_into_a_checklist():
    got = runs.parse_items(ITEMS + '- [x] **F1 Old.** Was done.\n  more words\n')
    assert [(i['id'], i['title'], i['acceptance'], i['ticked']) for i in got] == [
        ('E1', 'Export link', 'a test.', False), ('E2', 'Export file', 'a test.', False), ('F1', 'Old', '', True)]
    assert got[2]['text'] == 'Was done. more words'
    assert runs.parse_items('') == [] and runs.parse_items(None) == []


def test_progress_gives_each_items_newest_state():
    older = {'items': {'E1': 'blocked'}, 'current': None}
    newer = {'items': {'E1': 'done', 'E2': 'running'}, 'current': 'E2'}
    assert runs.task_progress([newer, older], ITEMS)['states'] == {'E1': 'done', 'E2': 'running'}


def test_safe_next_only_returns_to_this_site():
    assert ui.safe_next('/my?project=alpha') == '/my?project=alpha'
    for bad in ('//evil.example', 'https://evil.example/', '/\\evil.example', 'my', '', None, '/a\r\nSet-Cookie: x'):
        assert ui.safe_next(bad) is None, bad
    assert ui.pr_label('https://github.com/o/r/pull/7') == '#7' and ui.pr_label('https://x.example/y') == 'https://x.example/y'


# ---------- My work ----------

def test_a_short_plan_is_approved_from_my_work_and_returns_there(app, team, conn):
    n = planned(app, team)
    page = team['clients']['manager'].get('/my?project=alpha').get_data(as_text=True)
    assert 'name="decision" value="approve" class="btn primary">Approve</button>' in page and '2 items' in page
    resp = post(team['clients']['manager'], '/projects/alpha/tasks/%d/decide' % n, decision='approve',
                next='/my?project=alpha')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/my?project=alpha')
    assert tasks.get(conn, team['alpha']['id'], n)['status'] == 'approved'


def test_a_long_plan_is_read_first_and_next_never_leaves_the_site(app, team, conn):
    n = planned(app, team, items=THREE)
    page = team['clients']['manager'].get('/my').get_data(as_text=True)
    assert 'value="approve"' not in page and 'class="btn primary" href="/projects/alpha/tasks/%d">Read plan' % n in page
    resp = post(team['clients']['manager'], '/projects/alpha/tasks/%d/decide' % n, decision='approve',
                next='//evil.example/x')
    assert resp.headers['Location'].endswith('/projects/alpha/tasks/%d' % n)


def test_my_work_filters_by_project_and_lists_each_task_once(app, team, conn):
    from central import projects
    beta = projects.create(conn, 'beta', 'Beta', '2026-11-23T09:00:00Z', 'https://github.com/example/beta.git')
    projects.add_member(conn, beta['id'], 'dev@example.com', 'developer', '2026-11-23T09:00:00Z')
    n, _ = building_task(app, team)
    send_run(app, team, n)
    page = team['clients']['dev'].get('/my').get_data(as_text=True)
    assert 'aria-current="page">All</a>' in page and '>Beta</a>' in page
    assert page.count('href="/projects/alpha/tasks/%d">#%d CSV export' % (n, n)) == 1      # mine, not also "the team's"
    assert '1 of 2 items' in page and 'on E2' in page
    beta_only = team['clients']['dev'].get('/my?project=beta').get_data(as_text=True)
    assert 'CSV export' not in beta_only and 'aria-current="page">Beta</a>' in beta_only
    unknown = team['clients']['dev'].get('/my?project=nope').get_data(as_text=True)
    assert 'aria-current="page">All</a>' in unknown and 'CSV export' in unknown


def test_my_work_warns_when_a_runner_went_quiet(app, team, conn, clock):
    import datetime
    n, _ = building_task(app, team)
    from .test_access import signed_in
    clock.now += datetime.timedelta(hours=3)
    page = signed_in(app, 'dev@example.com').get('/my').get_data(as_text=True)     # a fresh session after 3 hours
    assert 'Its runner has been quiet since' in page


# ---------- Tasks list ----------

def test_tasks_list_tabs_mine_and_search(app, team, conn):
    planned(app, team, title='CSV export')
    add(team, title='Dark sidebar')       # not "Dark mode": the top bar's theme button carries that text on every page
    add(team, title='Admin report', who='admin', assignee=team['accounts']['dev']['id'])
    add(team, title='Owner thing', who='owner')
    c = team['clients']['dev']
    assert 'class="board"' in c.get('/projects/alpha/tasks').get_data(as_text=True)   # the board is the default
    page = c.get('/projects/alpha/tasks?view=list').get_data(as_text=True)         # opens on the first tab with tasks
    assert 'aria-current="page">Needs approval <span class="count">1</span>' in page and 'CSV export' in page
    assert 'Open <span class="count">3</span>' in page and 'Dark sidebar' not in page
    page = c.get('/projects/alpha/tasks?state=open&mine=1').get_data(as_text=True)
    assert 'Dark sidebar' in page and 'Admin report' in page and 'Owner thing' not in page
    assert 'Open <span class="count">2</span>' in page and 'mine=1' in page        # tabs keep the filter
    page = c.get('/projects/alpha/tasks?state=open&q=DARK').get_data(as_text=True)
    assert 'Dark sidebar' in page and 'Admin report' not in page
    assert 'No task here matches.' in c.get('/projects/alpha/tasks?state=done&q=zzz').get_data(as_text=True)


def test_an_empty_project_offers_the_first_task(team):
    page = team['clients']['dev'].get('/projects/alpha/tasks?view=list').get_data(as_text=True)
    assert 'No tasks yet.' in page and 'href="/projects/alpha/tasks/new">Add the first task' in page
    assert 'Add the first task' not in team['clients']['viewer'].get('/projects/alpha/tasks?view=list').get_data(as_text=True)


# ---------- New task ----------

def test_new_task_page_and_done_when(team, conn):
    c = team['clients']['dev']
    assert 'href="/projects/alpha/tasks/new">New task' in c.get('/projects/alpha').get_data(as_text=True)
    page = c.get('/projects/alpha/tasks/new').get_data(as_text=True)
    assert 'How we know it' in page and 'value="pool"' in page                       # me or the pool
    assert team['accounts']['viewer']['id'] not in page                              # only admins choose someone else
    assert 'name="assignee"' in team['clients']['admin'].get('/projects/alpha/tasks/new').get_data(as_text=True)
    assert team['clients']['viewer'].get('/projects/alpha/tasks/new').status_code == 403
    resp = post(c, '/projects/alpha/tasks', title='CSV', description='Users ask.', done_when='A test downloads it.')
    row = tasks.get(conn, team['alpha']['id'], int(resp.headers['Location'].rsplit('/', 1)[1]))
    assert row['description'] == 'Users ask.\n\nDone when:\nA test downloads it.'
    assert tasks.with_done_when('', 'x') == 'Done when:\nx' and tasks.with_done_when('a', ' ') == 'a'


def test_a_refused_task_keeps_what_was_typed(team):
    resp = post(team['clients']['dev'], '/projects/alpha/tasks', title='', description='Kept words', done_when='Kept too')
    page = resp.get_data(as_text=True)
    assert resp.status_code == 400 and 'Kept words' in page and 'Kept too' in page and 'New task' in page


# ---------- task page ----------

def test_the_decision_bar_is_for_deciders_only(app, team):
    n = planned(app, team)
    page = team['clients']['dev'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'Approve plan' in page and 'Request changes…' in page and 'textarea id="d-comment" name="comment"' in page
    assert 'data-confirm="Reject task #%d CSV export?"' % n in page
    assert 'aria-current="step"><span class="step-dot" aria-hidden="true"></span>Approval' in page
    assert '<strong>CSV</strong>' in page                                        # the plan is rendered, escaped
    viewer = team['clients']['viewer'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'Approve plan' not in viewer and 'The plan waits for approval' in viewer


def test_items_show_their_live_state_and_the_page_refreshes_while_building(app, team):
    n, _ = building_task(app, team)
    send_run(app, team, n)
    page = team['clients']['viewer'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert '<li class="item-done">' in page and '<li class="item-running">' in page
    assert '<span>Done when</span> a test.' in page and 'data-refresh="30"' in page


def test_activity_tells_the_tasks_story(app, team, conn):
    n, claim = building_task(app, team)
    api(app, 'POST', '/api/v1/tasks/%d/report' % n, team['token']['dev'],
        {'claim': claim, 'result': 'done', 'pr_url': 'https://github.com/o/r/pull/7'})
    page = team['clients']['viewer'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    for text in ('Dev added this task', "Dev&#39;s runner posted a plan for this task", 'Dev approved the plan for this task',
                 "Dev&#39;s runner finished this task"):
        assert text in page, text
    assert '>#7</a>' in page                                                     # the side panel's pull request
    assert len(audit.rows(conn, 'task.plan.post')) == 1 and len(audit.rows(conn, 'task.report')) == 1
    other = add(team, title='Other')
    assert 'Dev added this task' in team['clients']['viewer'].get('/projects/alpha/tasks/%d' % other).get_data(as_text=True)
    assert 'posted a plan' not in team['clients']['viewer'].get('/projects/alpha/tasks/%d' % other).get_data(as_text=True)


def test_admin_actions_sit_in_the_more_menu(app, team):
    n = planned(app, team)
    page = team['clients']['admin'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert '<details class="more">' in page and 'Take back from runner' in page
    assert '<details class="more">' in team['clients']['owner'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert '<details class="more">' not in team['clients']['viewer'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)


def test_a_rejected_task_says_so_instead_of_steps(app, team):
    n = planned(app, team)
    decide(team, n, 'reject', who='owner')
    page = team['clients']['viewer'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'This task was rejected and is closed.' in page and 'class="steps"' not in page
