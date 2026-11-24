"""Central service: the bell in the top bar and the Notifications page (queue item N2)."""
import re

import pytest

from central import notifications, projects

from .test_access import post
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)

LATER = '2026-11-23T10:00:00Z'


def note(conn, world, who, text, link='/projects/alpha/tasks/1', dedupe=None, at=AT, project=None):
    project = project or world['alpha']
    done = notifications.notify(conn, [world['accounts'][who]['id']], project, 'plan', text, link,
                                dedupe or 'k:' + text, at)
    conn.commit()
    assert done == [world['accounts'][who]['id']]
    return conn.query_one('SELECT id FROM notification WHERE account_id = ? AND text = ?',
                          (world['accounts'][who]['id'], text))['id']


def bell(page):
    """The bell link in the top bar, or None."""
    m = re.search(r'<a class="bell"[^>]*>.*?</a>', page, re.S)
    return m.group(0) if m else None


def bell_count(page):
    m = re.search(r'class="pill attention bell-count">(\d+)<', bell(page) or '')
    return int(m.group(1)) if m else None


def page_of(world, who, path='/notifications'):
    resp = world['clients'][who].get(path)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


@pytest.fixture
def beta(world, conn):
    p = projects.create(conn, 'beta', 'Beta Shop', AT, 'https://github.com/example/beta')
    projects.add_member(conn, p['id'], 'owner@example.com', 'owner', AT)
    return p


def test_the_bell_shows_the_count_and_hides_it_at_zero(world, conn):
    page = page_of(world, 'owner', '/my')
    assert bell(page) is not None and '/notifications' in bell(page)
    assert bell_count(page) is None                              # nothing waiting: no count
    note(conn, world, 'owner', 'Plan waiting: #1 One')
    note(conn, world, 'owner', 'Plan waiting: #2 Two')
    assert bell_count(page_of(world, 'owner', '/my')) == 2
    assert bell_count(page_of(world, 'owner', '/projects')) == 2  # on every signed-in page
    assert bell_count(page_of(world, 'manager', '/my')) is None    # someone else's count is not mine


def test_the_bell_is_not_shown_signed_out(app):
    from .test_access import new_client
    assert bell(new_client(app).get('/signin').get_data(as_text=True)) is None


def test_a_resolved_notification_is_shown_as_done_and_not_counted(world, conn):
    note(conn, world, 'owner', 'Plan waiting: #1 Open one', dedupe='a')
    note(conn, world, 'owner', 'Plan waiting: #2 Finished one', dedupe='b')
    notifications.resolve(conn, 'b', LATER)
    conn.commit()
    page = page_of(world, 'owner')
    assert bell_count(page) == 1
    waiting, done = page.split('<h2>Done</h2>')
    assert 'Open one' in waiting and 'Finished one' not in waiting
    assert 'Finished one' in done and '>Done<' in done


def test_the_page_lists_only_the_persons_own_newest_first_with_project_and_time(world, conn, beta):
    note(conn, world, 'owner', 'Plan waiting: #1 Older', at='2026-11-23T08:00:00Z')
    note(conn, world, 'owner', 'Blocker waiting: Newer?', project=beta, link='/projects/beta/blockers/B1',
         at='2026-11-23T09:30:00Z')
    note(conn, world, 'manager', 'Plan waiting: #3 Not yours')
    page = page_of(world, 'owner')
    assert 'Not yours' not in page
    assert page.index('Newer?') < page.index('Older')
    assert 'Beta Shop' in page and 'Alpha' in page
    assert '<time datetime=' in page
    assert 'Older' not in page_of(world, 'manager') and 'Not yours' in page_of(world, 'manager')


def test_opening_marks_read_and_redirects_to_the_link(world, conn):
    nid = note(conn, world, 'owner', 'Plan waiting: #1 Go', link='/projects/alpha/tasks/1')
    resp = post(world['clients']['owner'], '/notifications/%s/open' % nid)
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/projects/alpha/tasks/1')
    row = conn.query_one('SELECT read_at, resolved_at FROM notification WHERE id = ?', (nid,))
    assert row['read_at'] and row['resolved_at'] is None
    assert bell_count(page_of(world, 'owner', '/my')) is None


def test_opening_never_leaves_the_site(world, conn):
    nid = note(conn, world, 'owner', 'Plan waiting: #1 Odd', link='//evil.example/x')
    resp = post(world['clients']['owner'], '/notifications/%s/open' % nid)
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/notifications')
    assert 'evil' not in resp.headers['Location']


def test_another_persons_id_gets_404_and_stays_unread(world, conn):
    nid = note(conn, world, 'manager', 'Plan waiting: #1 Theirs')
    assert post(world['clients']['owner'], '/notifications/%s/open' % nid).status_code == 404
    assert post(world['clients']['owner'], '/notifications/nope/open').status_code == 404
    assert conn.query_one('SELECT read_at FROM notification WHERE id = ?', (nid,))['read_at'] is None


def test_read_all_clears_the_count_only_for_that_person(world, conn):
    note(conn, world, 'owner', 'Plan waiting: #1 A')
    note(conn, world, 'owner', 'Plan waiting: #2 B')
    note(conn, world, 'manager', 'Plan waiting: #3 C')
    assert 'Mark all as read' in page_of(world, 'owner')
    resp = post(world['clients']['owner'], '/notifications/read-all')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/notifications')
    page = page_of(world, 'owner')
    assert bell_count(page) is None and 'Mark all as read' not in page
    assert 'A' in page                                            # still listed, now read
    assert notifications.unread_count(conn, world['accounts']['manager']['id']) == 1


def test_forms_carry_csrf_and_posts_without_it_are_refused(world, conn):
    nid = note(conn, world, 'owner', 'Plan waiting: #1 X')
    page = page_of(world, 'owner')
    forms = re.findall(r'<form method="post" action="/notifications/[^"]*">.*?</form>', page, re.S)
    assert len(forms) == 2                                         # open + read-all
    assert all('name="csrf_token"' in f for f in forms)
    c = world['clients']['owner']
    assert c.post('/notifications/%s/open' % nid).status_code == 400
    assert c.post('/notifications/read-all').status_code == 400
    assert notifications.unread_count(conn, world['accounts']['owner']['id']) == 1


def test_signed_out_is_sent_to_sign_in(app):
    from .test_access import new_client
    c = new_client(app)
    resp = c.get('/notifications')
    assert resp.status_code == 302 and '/signin' in resp.headers['Location']


def test_empty_page(world):
    page = page_of(world, 'viewer')
    assert 'Nothing is waiting on you.' in page and '<h2>Done</h2>' not in page
