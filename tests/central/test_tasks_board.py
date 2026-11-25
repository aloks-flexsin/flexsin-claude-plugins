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


def test_empty_project_and_empty_filter(team):
    page = team['clients']['dev'].get(URL).get_data(as_text=True)
    assert 'No tasks yet.' in page and 'class="board"' not in page
    add(team, title='Dark sidebar')
    page = team['clients']['dev'].get(URL + '?q=zzz').get_data(as_text=True)
    assert page.count('Nothing here.') == 6 and page.count('class="board-col"') == 6
