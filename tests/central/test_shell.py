"""Central service: the shared frame (UX audit, PR 2). Signed in, `/` is My work; the top bar shows the decisions waiting
for you; every project page has the same header and tabs with counts; a project opens on its Overview; settings
(members, repository, machine tokens) live under Settings, and the old URLs redirect."""
import datetime

from central import accounts, audit, projects, tasks

from .test_access import post
from .test_repo_url import AT, REPO, conn, world  # noqa: F401  (fixtures)

NOW = datetime.datetime.now(datetime.timezone.utc)


def owner(conn):
    return accounts.find_by_email(conn, 'owner@example.com')


def plan_waiting(world, conn, title='CSV export'):
    o = owner(conn)
    t = tasks.create(conn, world['alpha'], o, o, title, 'Users need it.', NOW)
    _, claim = tasks.take_next(conn, world['alpha'], o, 'alok/bhawish-claude-auto-runner', NOW)
    tasks.post_plan(conn, world['alpha'], t['number'], claim, '## Intent\nCSV.', '- [ ] **E1 x.**', NOW)
    return t


def test_home_is_my_work(world):
    resp = world['clients']['viewer'].get('/')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/my')


def test_top_bar_marks_the_page_and_counts_decisions(world, conn):
    plan_waiting(world, conn)
    page = world['clients']['owner'].get('/my').get_data(as_text=True)
    assert 'aria-current="page">My work <span class="pill attention" title="decisions waiting for you">1</span>' in page
    assert 'class="account-menu"' in page and 'owner@example.com' in page
    viewer = world['clients']['viewer'].get('/my').get_data(as_text=True)
    assert 'decisions waiting for you' not in viewer                 # viewers decide nothing
    assert '/admin/accounts' not in viewer and '/admin/accounts' in world['clients']['admin'].get('/my').get_data(as_text=True)


def test_every_project_page_has_the_same_header_and_tabs(world, conn):
    plan_waiting(world, conn)
    c = world['clients']['owner']
    pages = {'overview': '/projects/alpha', 'tasks': '/projects/alpha/tasks', 'tasks#1': '/projects/alpha/tasks/1',
             'runs': '/projects/alpha/runs', 'blockers': '/projects/alpha/blockers',
             'settings': '/projects/alpha/settings/members', 'settings#repo': '/projects/alpha/settings/repository',
             'settings#tokens': '/projects/alpha/settings/tokens'}
    labels = {'overview': 'Overview', 'tasks': 'Tasks', 'runs': 'Runs', 'blockers': 'Blockers', 'settings': 'Settings'}
    for key, url in pages.items():
        html = c.get(url).get_data(as_text=True)
        assert '<nav class="tabs" aria-label="Project">' in html, url
        assert 'aria-current="page">%s' % labels[key.split('#')[0]] in html, url
        assert 'title="plans waiting for approval">1</span>' in html, url
        assert 'You are <strong>owner</strong>' in html, url


def test_overview_shows_what_needs_attention_and_recent_activity(world, conn):
    plan_waiting(world, conn)
    audit.record(conn, 'task.create', AT, account_id=owner(conn)['id'], project_id=world['alpha']['id'],
                 detail={'number': 1, 'assignee': 'owner@example.com'})
    conn.commit()
    page = world['clients']['owner'].get('/projects/alpha').get_data(as_text=True)
    assert 'Needs attention' in page and '#1 CSV export' in page and 'Review plan' in page
    assert 'Owner added task #1' in page
    viewer = world['clients']['viewer'].get('/projects/alpha').get_data(as_text=True)
    assert 'View plan' in viewer and 'Review plan' not in viewer                # viewers look, deciders act
    assert 'data-refresh="30"' in page


def test_an_empty_overview_says_so(world):
    page = world['clients']['viewer'].get('/projects/alpha').get_data(as_text=True)
    assert 'Nothing needs anyone right now.' in page and 'No runner is working on this project right now.' in page


def test_settings_pages_and_old_urls(world, conn):
    c = world['clients']['owner']
    assert REPO in c.get('/projects/alpha/settings/repository').get_data(as_text=True)
    assert 'Change repository URL' in c.get('/projects/alpha/settings/repository').get_data(as_text=True)
    assert 'Change repository URL' not in world['clients']['viewer'].get('/projects/alpha/settings/repository').get_data(as_text=True)
    old = world['clients']['manager'].get('/projects/alpha/tokens')
    assert old.status_code == 301 and old.headers['Location'].endswith('/projects/alpha/settings/tokens')
    assert world['clients']['viewer'].get('/projects/alpha/settings/tokens').status_code == 404
    resp = post(c, '/projects/alpha/repo', repo_url='https://github.com/example/alpha2.git')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/projects/alpha/settings/repository')


def test_projects_list_is_cards_with_what_waits(world, conn):
    plan_waiting(world, conn)
    page = world['clients']['owner'].get('/projects').get_data(as_text=True)
    assert 'class="project-card" href="/projects/alpha"' in page and '1 plan to approve' in page


def test_strangers_still_get_404_everywhere(world, conn):
    accounts.create(conn, 'nobody@example.com', 'violet-anchor-58-tide')
    from .test_access import signed_in
    stranger = signed_in(world['clients']['owner'].application, 'nobody@example.com')
    for url in ('/projects/alpha', '/projects/alpha/settings/members', '/projects/alpha/settings/repository'):
        assert stranger.get(url).status_code == 404, url
