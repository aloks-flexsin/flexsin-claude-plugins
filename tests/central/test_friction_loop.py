"""Central service: closing the loop on a drafted friction task (decision record 0011, R9)."""
import datetime
import json

from central import accounts, frictions, tasks

from .test_access import PW, post, signed_in
from .test_friction_drafts import SIG, at, drafted, home, push, sig  # noqa: F401  (fixtures and helpers)
from .test_ingest import START, clock, conn, event, friction, send, world  # noqa: F401  (fixtures)


def owner(home):
    return home['accounts']['owner']


def finish(conn, home, number, moment):
    """The owner's runner takes the drafted task and reports it done."""
    task, claim = tasks.take_next(conn, home['home'], owner(home), 'laptop', moment)
    assert task['number'] == number
    conn.execute("UPDATE project_task SET status = 'building' WHERE id = ?", (task['id'],))
    conn.commit()
    return tasks.report(conn, home['home'], number, claim, 'done', 'b', None, 'fixed it', moment)


def test_rejecting_the_drafted_plan_mutes_it_and_later_events_draft_nothing(home, conn, clock):
    push(home, 3)
    [task] = drafted(conn, home)
    conn.execute("UPDATE project_task SET status = 'plan_review' WHERE id = ?", (task['id'],))
    conn.commit()
    tasks.decide(conn, home['home'], tasks.get(conn, home['home']['id'], task['number']), owner(home), 'reject',
                 'not worth it', START)
    s = sig(conn)
    assert (s['state'], s['reason'], s['changed_by']) == ('muted', 'task #%d rejected' % task['number'],
                                                          owner(home)['id'])
    [row] = conn.query("SELECT * FROM audit WHERE action = 'friction.mute'")
    assert json.loads(row['detail'])['via'] == 'task'
    at(clock, days=1)
    push(home, 5)
    assert len(drafted(conn, home)) == 1 and sig(conn)['state'] == 'muted'


def test_closing_the_open_drafted_task_mutes_it_too(home, conn, clock):
    push(home, 3)
    [task] = drafted(conn, home)
    client = signed_in(home['app'], 'owner@example.com')
    resp = post(client, '/projects/auto-runner/tasks/%d/close' % task['number'], reason='Not ours', confirm='1')
    assert resp.status_code in (302, 303)
    assert tasks.get(conn, home['home']['id'], task['number'])['status'] == 'rejected'
    assert (sig(conn)['state'], sig(conn)['reason']) == ('muted', 'task #%d rejected' % task['number'])
    at(clock, days=2)
    push(home, 4)
    assert len(drafted(conn, home)) == 1


def test_a_done_task_marks_it_fixed_and_it_can_come_back(home, conn, clock):
    push(home, 3)
    [first] = drafted(conn, home)
    at(clock, minutes=30)
    finish(conn, home, first['number'], clock.now)
    s = sig(conn)
    assert (s['state'], s['changed_at'], s['task_number']) == ('fixed', '2026-10-31T09:30:00Z', first['number'])
    assert conn.query("SELECT * FROM audit WHERE action = 'friction.fixed'")
    at(clock, minutes=40)
    push(home, 3)                                          # the fix day itself is left out
    assert len(drafted(conn, home)) == 1 and sig(conn)['state'] == 'fixed'
    at(clock, days=1)
    push(home, 2)                                          # old ones (before the fix) do not help reach 3
    assert len(drafted(conn, home)) == 1
    at(clock, days=1, minutes=5)
    push(home)
    first_again, second = drafted(conn, home)
    assert second['title'] == 'Runner friction came back: %s' % SIG and second['status'] == 'open'
    assert 'task #%d' % first['number'] in second['description'] and '3 times in 1 project' in second['description']
    s = sig(conn)
    assert (s['state'], s['task_number'], s['previous_task_number']) == ('drafted', second['number'], first['number'])
    detail = json.loads(conn.query("SELECT detail FROM audit WHERE action = 'friction.draft' ORDER BY at DESC")[0]['detail'])
    assert detail['previous_task'] == first['number']

    admin = accounts.create(conn, 'admin@example.com', PW, role='admin')
    conn.commit()
    page = signed_in(home['app'], admin['email']).get('/frictions').get_data(as_text=True)
    assert 'Drafted' in page
    assert '/projects/auto-runner/tasks/%d"' % second['number'] in page
    assert 'Earlier task #%d' % first['number'] in page
    assert '/projects/auto-runner/tasks/%d"' % first['number'] in page


def test_the_page_shows_fixed_with_its_task(home, conn, clock):
    push(home, 3)
    [task] = drafted(conn, home)
    finish(conn, home, task['number'], clock.now)
    admin = accounts.create(conn, 'admin@example.com', PW, role='admin')
    conn.commit()
    page = signed_in(home['app'], admin['email']).get('/frictions?state=fixed').get_data(as_text=True)
    assert 'Fixed' in page and '/projects/auto-runner/tasks/%d"' % task['number'] in page


def test_a_stopped_report_or_another_task_changes_nothing(home, conn, clock):
    push(home, 3)
    [task] = drafted(conn, home)
    got, claim = tasks.take_next(conn, home['home'], owner(home), 'laptop', clock.now)
    conn.execute("UPDATE project_task SET status = 'building' WHERE id = ?", (got['id'],))
    conn.commit()
    tasks.report(conn, home['home'], task['number'], claim, 'stopped', 'b', None, 'later', clock.now)
    assert sig(conn)['state'] == 'drafted'
    other = tasks.create(conn, home['home'], owner(home), owner(home), 'Other', 'x', clock.now)
    tasks.close(conn, home['home'], other, owner(home), 'no', clock.now)
    conn.commit()
    assert sig(conn)['state'] == 'drafted'
    assert frictions.task_ended(conn, home['alpha'], task, 'rejected', None, '2026-10-31T10:00:00Z') is None
