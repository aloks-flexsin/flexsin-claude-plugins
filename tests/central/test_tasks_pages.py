"""Task pages: what the task page and the task list say about a merged task."""
from central import tasks

from .test_repo_url import conn, world  # noqa: F401  (fixtures)
from .test_tasks import add, clock, team  # noqa: F401  (fixtures)


def test_merged_task_shows_merged_time(app, team, conn):
    n = add(team, title='Shipped thing')
    other = add(team, title='Open thing')
    conn.execute("UPDATE project_task SET status = 'done', pr_url = ?, merged_at = ? WHERE number = ?",
                 ('https://github.com/o/r/pull/3', '2026-11-23T10:30:00Z', n))
    conn.commit()
    assert tasks.get(conn, team['alpha']['id'], n)['merged_at']
    client = team['clients']['dev']
    page = client.get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'Merged 23 Nov, 10:30' in page
    for url in ('/projects/alpha/tasks?state=done&view=list', '/projects/alpha/tasks?view=board&feed=history'):
        listing = client.get(url).get_data(as_text=True)
        assert 'Shipped thing' in listing and 'Merged 23 Nov, 10:30' in listing, url
    assert 'Merged' not in client.get('/projects/alpha/tasks/%d' % other).get_data(as_text=True)
