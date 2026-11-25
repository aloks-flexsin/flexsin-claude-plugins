"""Central service: before `python -m central init-db` adds the friction tables, the requests that count frictions or
end a drafted task still succeed; only the friction bookkeeping is skipped (hotfix after #45)."""
import pytest

from central import frictions, tasks

from .test_access import post
from .test_frictions import people, waiting_task  # noqa: F401  (fixtures)
from .test_ingest import START, clock, conn, event, friction, send, world  # noqa: F401  (fixtures)

FRICTION_TABLES = ('friction_count', 'friction_day', 'friction_signature')


def drop_friction_tables(conn):
    for table in FRICTION_TABLES:
        conn.execute('DROP TABLE %s' % table)
    conn.commit()


def test_a_friction_event_is_stored_without_the_tables(world, conn):
    drop_friction_tables(conn)
    resp = send(world['app'], world['token'], [event(), friction()])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()['accepted'] == 2
    assert conn.query_one('SELECT COUNT(*) AS n FROM event')['n'] == 2


def test_a_plan_sent_back_without_the_tables(people, conn):
    drop_friction_tables(conn)
    n = waiting_task(conn, people)
    resp = post(people['clients']['owner'], '/projects/alpha/tasks/%d/decide' % n, decision='changes',
                comment='Smaller.')
    assert resp.status_code == 302, resp.get_data(as_text=True)
    assert tasks.get(conn, people['alpha']['id'], n)['status'] == 'changes_requested'


def test_a_rejected_plan_without_the_tables(people, conn):
    drop_friction_tables(conn)
    n = waiting_task(conn, people)
    resp = post(people['clients']['owner'], '/projects/alpha/tasks/%d/decide' % n, decision='reject', confirm='1')
    assert resp.status_code == 302, resp.get_data(as_text=True)
    assert tasks.get(conn, people['alpha']['id'], n)['status'] == 'rejected'


def test_task_ended_and_record_return_none_without_the_tables(people, conn):
    drop_friction_tables(conn)
    row = {'number': 1}
    assert frictions.task_ended(conn, people['alpha'], row, 'done', None, '2026-10-31T09:00:00Z') is None
    assert frictions.record(conn, people['alpha'], 'retry', 'exit_1', '2026-10-31T09:00:00Z') is None
    # the connection is still usable: the savepoint was rolled back, not the whole transaction
    assert conn.query_one('SELECT COUNT(*) AS n FROM project')['n'] >= 1


def test_other_errors_are_still_raised(people, conn, monkeypatch):
    def broken(db, *args):
        raise ValueError('not a schema problem')
    monkeypatch.setattr(frictions, '_record', broken)
    with pytest.raises(ValueError):
        frictions.record(conn, people['alpha'], 'retry', 'exit_1', '2026-10-31T09:00:00Z')


def test_with_the_tables_counting_still_works(world, conn):
    assert send(world['app'], world['token'], friction()).status_code == 200
    assert conn.query_one('SELECT count FROM friction_count')['count'] == 1


def test_a_task_is_reported_done_without_the_tables(people, conn):
    owner = people['accounts']['owner']
    tasks.create(conn, people['alpha'], owner, owner, 'CSV export', 'Export.', START, status='approved',
                 plan='## Intent\nCSV.', items='- [ ] E1')
    conn.commit()
    task, claim = tasks.take_next(conn, people['alpha'], owner, 'laptop', START)
    drop_friction_tables(conn)
    row = tasks.report(conn, people['alpha'], task['number'], claim, 'done', 'b', None, 'built', START)
    assert row['status'] == 'done'
    assert tasks.get(conn, people['alpha']['id'], task['number'])['status'] == 'done'
