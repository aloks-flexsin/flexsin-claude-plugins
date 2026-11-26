"""Central service: the Tasks page opens on a board of six columns, with a Board | List toggle."""
import re

from central import tasks

from .test_repo_url import conn, world  # noqa: F401  (fixtures)
from .test_tasks import add, clock, team  # noqa: F401  (fixtures)

URL = '/projects/alpha/tasks'
HEADINGS = ['Open', 'Planning', 'Needs approval', 'Ready', 'Building', 'Done']


def set_status(conn, team, number, status):
    conn.execute('UPDATE project_task SET status = ? WHERE project_id = ? AND number = ?', (status, team['alpha']['id'], number))
    conn.commit()


def column(page, key):
    return re.search(r'<section class="board-col" aria-labelledby="col-%s">(.*?)</section>' % key, page, re.S).group(1)


def spread(conn, team):
    """One task per status, titled by its status."""
    numbers = {}
    for status in tasks.STATUSES:
        numbers[status] = add(team, title='T-' + status.replace('_', '-'))
        set_status(conn, team, numbers[status], status)
    return numbers


def test_every_status_is_in_exactly_one_column():
    listed = [s for _, _, statuses in tasks.BOARD_COLUMNS for s in statuses]
    assert sorted(listed) == sorted(tasks.STATUSES)
    assert [label for _, label, _ in tasks.BOARD_COLUMNS] == HEADINGS


def test_the_board_shows_each_task_in_its_columns_with_counts(team, conn):
    spread(conn, team)
    page = team['clients']['viewer'].get(URL).get_data(as_text=True)
    assert [h for h in re.findall(r'<h2 id="col-\w+">([A-Za-z ]+?) <span', page)] == HEADINGS
    expect = {'open': ['T-open'], 'planning': ['T-planning', 'T-changes-requested'], 'approval': ['T-plan-review'],
              'ready': ['T-approved', 'T-queued'], 'building': ['T-building'], 'done': ['T-done', 'T-rejected']}
    for key, titles in expect.items():
        col = column(page, key)
        assert '<span class="count">%d</span>' % len(titles) in col
        for title in titles:
            assert title in col
        assert sum(t in col for t in expect_all(expect)) == len(titles)


def expect_all(expect):
    return [t for titles in expect.values() for t in titles]


def test_list_view_keeps_the_tabs_and_no_board(team, conn):
    spread(conn, team)
    page = team['clients']['dev'].get(URL + '?view=list').get_data(as_text=True)
    assert 'class="state-tabs"' in page and 'class="board"' not in page
    page = team['clients']['dev'].get(URL + '?state=done').get_data(as_text=True)
    assert 'aria-current="page">Done <span class="count">2</span>' in page and 'class="board"' not in page


def test_mine_and_unknown_view(team, conn):
    add(team, title='Dev own')
    add(team, title='Owner own', who='owner')
    c = team['clients']['dev']
    page = c.get(URL + '?view=board&mine=1').get_data(as_text=True)
    assert 'Dev own' in page and 'Owner own' not in page
    page = c.get(URL + '?view=nonsense').get_data(as_text=True)
    assert 'class="board"' in page and 'Owner own' in page


def test_toggle_links_keep_q_and_mine_and_the_form_stays_on_the_board(team):
    add(team, title='Dark sidebar')
    page = team['clients']['dev'].get(URL + '?view=board&mine=1&q=x').get_data(as_text=True)
    toggle = re.search(r'<nav class="view-toggle".*?</nav>', page, re.S).group(0)
    assert 'aria-current="page">Board' in toggle
    for href in re.findall(r'href="([^"]+)"', toggle):
        assert 'mine=1' in href and 'q=x' in href
    assert 'name="view" value="board"' in page and 'name="state"' not in page
    assert 'href="%s?view=board"' % URL in page            # Clear
    assert 'class="board"' in page


def cookie_of(response):
    return [h for h in response.headers.getlist('Set-Cookie') if h.startswith('task_view=')]


def test_view_choice_is_remembered_in_a_cookie(team):
    add(team, title='Dark sidebar')
    c = team['clients']['dev']
    set_cookie = cookie_of(c.get(URL + '?view=list'))
    assert len(set_cookie) == 1 and 'task_view=list' in set_cookie[0]
    for flag in ('HttpOnly', 'Secure', 'SameSite=Lax', 'Path=/', 'Max-Age=31536000'):
        assert flag in set_cookie[0]
    page = c.get(URL, headers={'Cookie': 'task_view=list'}).get_data(as_text=True)
    assert 'class="state-tabs"' in page and 'class="board"' not in page
    assert 'class="board"' in c.get(URL + '?view=board', headers={'Cookie': 'task_view=list'}).get_data(as_text=True)
    assert 'task_view=board' in cookie_of(c.get(URL + '?view=board'))[0]


def test_bad_cookie_shows_the_board_and_state_leaves_the_cookie(team):
    add(team, title='Dark sidebar')
    c = team['clients']['dev']
    response = c.get(URL, headers={'Cookie': 'task_view=grid'})
    assert 'class="board"' in response.get_data(as_text=True) and not cookie_of(response)
    response = c.get(URL + '?state=open', headers={'Cookie': 'task_view=board'})
    assert 'class="state-tabs"' in response.get_data(as_text=True) and not cookie_of(response)


def test_done_column_is_capped_at_twenty(team, conn):
    for i in range(25):
        n = add(team, title='Finished %02d' % i, who='dev' if i < 22 else 'owner')
        set_status(conn, team, n, 'done')
    c = team['clients']['dev']
    done = column(c.get(URL).get_data(as_text=True), 'done')
    assert done.count('class="board-card"') == 20 and '<span class="count">25</span>' in done
    assert 'Show all 25 in the list' in done and 'view=list&amp;state=done' in done
    done = column(c.get(URL + '?mine=1').get_data(as_text=True), 'done')
    assert '<span class="count">22</span>' in done and done.count('class="board-card"') == 20
    assert 'Show all 22 in the list' in done and 'mine=1' in done
    assert 'Show all' not in column(c.get(URL + '?q=Finished 0').get_data(as_text=True), 'done')


def test_empty_project_and_empty_filter(team):
    page = team['clients']['dev'].get(URL).get_data(as_text=True)
    assert 'No tasks yet.' in page and 'class="board"' not in page
    add(team, title='Dark sidebar')
    page = team['clients']['dev'].get(URL + '?q=zzz').get_data(as_text=True)
    assert page.count('Nothing here.') == 6 and page.count('class="board-col"') == 6


def test_board_has_recent_activity_and_runners_below_it_but_the_list_does_not(team, conn):
    spread(conn, team)
    page = team['clients']['viewer'].get(URL).get_data(as_text=True)
    assert 'aria-label="Recent activity"' in page and 'id="runners"' in page and 'All runs' in page
    assert page.index('id="col-done"') < page.index('aria-label="Recent activity"')
    listing = team['clients']['viewer'].get(URL + '?view=list').get_data(as_text=True)
    assert 'aria-label="Recent activity"' not in listing
