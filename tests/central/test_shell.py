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
    assert 'aria-current="page"><span class="grow">My work</span><span class="pill attention" title="decisions waiting for you">1</span>' in page
    assert 'data-updated>Updated just now' in page and 'Log out</button>' in page
    assert 'class="account-menu"' in page and 'owner@example.com' in page
    viewer = world['clients']['viewer'].get('/my').get_data(as_text=True)
    assert 'decisions waiting for you' not in viewer                 # viewers decide nothing
    assert '/admin/accounts' not in viewer and '/admin/accounts' in world['clients']['admin'].get('/my').get_data(as_text=True)


def test_the_board_has_the_project_head_and_other_pages_a_breadcrumb(world, conn):
    plan_waiting(world, conn)
    c = world['clients']['owner']
    board = c.get('/projects/alpha/tasks').get_data(as_text=True)
    assert '<nav class="head-actions" aria-label="Project">' in board and 'You are <strong>owner</strong>' in board
    assert '>Project settings</a>' in board and 'href="/projects/alpha/tasks/new">New task' in board
    assert '<nav class="tabs"' not in board
    titles = {'/projects/alpha/runs': 'Runs', '/projects/alpha/blockers': 'Blockers',
              '/projects/alpha/settings/members': 'Project settings', '/projects/alpha/settings/repository': 'Project settings',
              '/projects/alpha/settings/tokens': 'Project settings', '/projects/alpha/tasks/new': 'New task'}
    for url, title in titles.items():
        html = c.get(url).get_data(as_text=True)
        assert '<div class="crumb page-crumb"><a href="/projects/alpha/tasks">Alpha</a> /' in html, url
        assert '<h1>%s</h1>' % title in html, url
        assert 'head-actions' not in html, url
    task = c.get('/projects/alpha/tasks/1').get_data(as_text=True)
    assert '<a href="/projects/alpha/tasks">Alpha</a> / <a href="/projects/alpha/tasks">Board</a> /' in task


def test_blockers_button_shows_the_waiting_count(world, conn):
    from central import blockers as _b  # noqa: F401
    page = world['clients']['viewer'].get('/projects/alpha/tasks').get_data(as_text=True)
    assert '>Blockers</a>' in page and 'class="btn blockers"' not in page        # nothing waits: a plain button


def test_the_project_address_opens_its_board(world):
    resp = world['clients']['viewer'].get('/projects/alpha')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/projects/alpha/tasks')


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
    assert 'class="project-card" href="/projects/alpha/tasks"' in page and '1 plan to approve' in page


def test_strangers_still_get_404_everywhere(world, conn):
    accounts.create(conn, 'nobody@example.com', 'violet-anchor-58-tide')
    from .test_access import signed_in
    stranger = signed_in(world['clients']['owner'].application, 'nobody@example.com')
    for url in ('/projects/alpha', '/projects/alpha/settings/members', '/projects/alpha/settings/repository'):
        assert stranger.get(url).status_code == 404, url


def test_projects_list_puts_the_ones_needing_you_first(world, conn):
    plan_waiting(world, conn)
    page = world['clients']['owner'].get('/projects').get_data(as_text=True)
    first = page.index('class="project-card"')
    assert 'aria-label="Waits for you"' in page[first:first + 700]


def test_settings_and_admin_pages_have_their_section_tabs(world):
    owner = world['clients']['owner'].get('/account').get_data(as_text=True)
    assert 'aria-label="Settings section"' in owner and 'aria-label="Admin section"' not in owner
    admin = world['clients']['admin'].get('/admin/accounts').get_data(as_text=True)
    assert 'aria-label="Admin section"' in admin and '>Notification log</a>' in admin


def test_sidebar_filters_my_work_per_project_with_markers(world, conn):
    from central import projects as _p
    beta = _p.create(conn, 'beta', 'Beta', AT, 'https://github.com/example/beta.git')
    _p.add_member(conn, beta['id'], 'owner@example.com', 'owner', AT)
    plan_waiting(world, conn)
    conn.commit()
    page = world['clients']['owner'].get('/my').get_data(as_text=True)
    side = page[page.index('<nav class="side"'):page.index('</nav>', page.index('<nav class="side"'))]
    alpha = side[side.index('href="/my?project=alpha"'):]
    assert 'aria-label="Waits for you"' in alpha[:alpha.index('</a>')]                 # alpha has a plan for the owner
    beta_link = side[side.index('href="/my?project=beta"'):]
    assert '<svg' not in beta_link[:beta_link.index('</a>')]                           # nothing in beta
    assert 'class="legend"' in side
    single = world['clients']['viewer'].get('/my').get_data(as_text=True)
    assert '/my?project=' not in single                                                # one project: no filters


def test_projects_search_and_device_page_without_sidebar(world, conn):
    from central import projects as _p
    _p.create(conn, 'beta', 'Beta', AT, 'https://github.com/example/beta.git')
    conn.commit()
    admin = world['clients']['admin']
    page = admin.get('/projects?q=alp').get_data(as_text=True)
    assert 'class="project-card" href="/projects/alpha/tasks"' in page and 'class="project-card" href="/projects/beta/tasks"' not in page
    assert 'data-open="create"' in page and 'id="create"' in page
    assert 'No project matches' in admin.get('/projects?q=zzz').get_data(as_text=True)
    assert '<nav class="side"' not in admin.get('/device').get_data(as_text=True)
    assert '<nav class="side"' in admin.get('/my').get_data(as_text=True)
