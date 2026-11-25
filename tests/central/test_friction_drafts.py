"""Central service: a signature that keeps coming back drafts a task (decision record 0011, R8)."""
import datetime
import json

import pytest

from central import accounts, config, frictions, projects, tasks

from .test_access import PW, post, signed_in
from .test_ingest import START, clock, conn, event, friction, send, world  # noqa: F401  (fixtures)

SIG = 'retry:exit_1'


@pytest.fixture
def home(world, conn):
    """The auto-runner project that drafted tasks go to, with two owners (the earlier one is first) and a developer."""
    project = projects.create(conn, 'auto-runner', 'Auto runner', '2026-10-01T09:00:00Z',
                              'https://github.com/example/auto-runner.git')
    accts = {who: accounts.create(conn, '%s@example.com' % who, PW)
             for who in ('owner', 'later', 'dev', 'viewer', 'outsider')}
    projects.add_member(conn, project['id'], 'owner@example.com', 'owner', '2026-10-01T09:00:00Z')
    projects.add_member(conn, project['id'], 'later@example.com', 'owner', '2026-10-02T09:00:00Z')
    projects.add_member(conn, project['id'], 'dev@example.com', 'developer', '2026-10-03T09:00:00Z')
    projects.add_member(conn, project['id'], 'viewer@example.com', 'viewer', '2026-10-03T09:00:00Z')
    conn.commit()
    world['app'].config['CENTRAL_REFLECTION_PROJECT'] = 'auto-runner'
    return dict(world, home=project, accounts=accts)


def at(clock, days=0, minutes=0):
    clock.now = START + datetime.timedelta(days=days, minutes=minutes)


def push(home, n=1, project='alpha', **detail):
    token = home['token'] if project == 'alpha' else home['beta_token']
    for _ in range(n):
        e = friction(**detail) if detail else friction()
        assert send(home['app'], token, dict(e, project=project)).status_code == 200


def drafted(conn, home):
    return conn.query('SELECT * FROM project_task WHERE project_id = ? ORDER BY number', (home['home']['id'],))


def sig(conn, name=SIG):
    return conn.query_one('SELECT * FROM friction_signature WHERE signature = ?', (name,))


def test_the_third_event_drafts_one_task_with_evidence_and_a_notification(home, conn, clock):
    push(home, 2)
    assert drafted(conn, home) == [] and sig(conn)['state'] == 'watching'
    at(clock, minutes=10)
    push(home)
    [task] = drafted(conn, home)
    owner = home['accounts']['owner']
    assert task['title'] == 'Runner friction: retry:exit_1' and task['status'] == 'open'
    assert task['assignee_id'] == owner['id'] and task['created_by'] == owner['id']
    for text in ('3 times in 1 project', 'First seen: 2026-10-31T09:00:00Z', 'last seen: 2026-10-31T09:10:00Z',
                 'early-exit retries', 'Kind: retry; subject: exit_1'):
        assert text in task['description']
    assert 'Alpha' not in task['description'] and 'alpha' not in task['description']   # no project names
    s = sig(conn)
    assert (s['state'], s['task_project_id'], s['task_number']) == ('drafted', home['home']['id'], task['number'])
    [note] = conn.query('SELECT * FROM notification')
    assert (note['account_id'], note['kind'], note['project_id']) == (owner['id'], 'assigned', home['home']['id'])
    assert note['link'] == '/projects/auto-runner/tasks/%d' % task['number']
    [row] = conn.query("SELECT * FROM audit WHERE action = 'friction.draft'")
    assert row['account_id'] == owner['id'] and row['project_id'] == home['home']['id']
    detail = json.loads(row['detail'])
    assert detail == {'signature': SIG, 'task': task['number'], 'count': 3, 'projects': 1, 'via': 'reflection'}


def test_the_fourth_event_drafts_nothing_more(home, conn):
    push(home, 4)
    assert len(drafted(conn, home)) == 1
    assert conn.query_one("SELECT COUNT(*) AS n FROM audit WHERE action = 'friction.draft'")['n'] == 1


def test_two_projects_reach_it_at_count_two(home, conn):
    push(home, 1, kind='permission_denied', subject='Bash:sed')
    assert drafted(conn, home) == []
    push(home, 1, project='beta', kind='permission_denied', subject='Bash:sed')
    [task] = drafted(conn, home)
    assert task['title'] == 'Runner friction: permission_denied:Bash:sed'
    assert '2 times in 2 projects' in task['description'] and '.claude/settings.json' in task['description']


def test_the_daily_cap_stops_the_fourth_draft_until_the_next_day(home, conn, clock):
    for code in (1, 2, 3, 4):
        push(home, 3, kind='retry', subject='exit_%d' % code)
    assert [t['title'] for t in drafted(conn, home)] == ['Runner friction: retry:exit_%d' % c for c in (1, 2, 3)]
    assert sig(conn, 'retry:exit_4')['state'] == 'watching'
    at(clock, minutes=600)                                 # still the same UTC day
    push(home, 1, kind='retry', subject='exit_4')
    assert len(drafted(conn, home)) == 3
    at(clock, days=1)
    push(home, 1, kind='retry', subject='exit_4')
    assert drafted(conn, home)[-1]['title'] == 'Runner friction: retry:exit_4'
    assert sig(conn, 'retry:exit_4')['state'] == 'drafted'


def test_the_daily_cap_comes_from_the_setting(home, conn):
    home['app'].config['CENTRAL_REFLECTION_DAILY'] = '1'
    for code in (1, 2):
        push(home, 3, kind='retry', subject='exit_%d' % code)
    assert len(drafted(conn, home)) == 1


def test_without_the_project_setting_nothing_is_drafted(home, conn):
    home['app'].config['CENTRAL_REFLECTION_PROJECT'] = None
    push(home, 5)
    assert drafted(conn, home) == [] and sig(conn)['state'] == 'watching'
    assert conn.query('SELECT * FROM notification') == []


def test_an_unknown_project_drafts_nothing(home, conn):
    home['app'].config['CENTRAL_REFLECTION_PROJECT'] = 'nowhere'
    push(home, 3)
    assert sig(conn)['state'] == 'watching'


def test_a_muted_signature_is_never_drafted(home, conn):
    home['app'].config['CENTRAL_REFLECTION_PROJECT'] = None
    push(home)
    frictions.mute(conn, SIG, None, '2026-10-31T09:00:00Z', 'known')
    conn.commit()
    home['app'].config['CENTRAL_REFLECTION_PROJECT'] = 'auto-runner'
    push(home, 5)
    assert drafted(conn, home) == [] and sig(conn)['state'] == 'muted'


def test_an_unmuted_signature_counts_only_later_days(home, conn, clock):
    home['app'].config['CENTRAL_REFLECTION_PROJECT'] = None
    push(home, 4)                                          # old occurrences, over the threshold
    frictions.mute(conn, SIG, None, '2026-10-31T09:05:00Z', 'known')
    frictions.unmute(conn, SIG, None, '2026-10-31T09:10:00Z')
    conn.commit()
    home['app'].config['CENTRAL_REFLECTION_PROJECT'] = 'auto-runner'
    at(clock, minutes=30)
    push(home, 2)                                          # the unmute day itself is left out
    assert drafted(conn, home) == []
    at(clock, days=1)
    push(home, 2)
    assert drafted(conn, home) == []
    at(clock, days=1, minutes=5)
    push(home)
    [task] = drafted(conn, home)
    assert '3 times in 1 project' in task['description']


def test_occurrences_older_than_30_days_do_not_count(home, conn, clock):
    push(home, 2)
    at(clock, days=30)
    push(home)
    assert drafted(conn, home) == []
    at(clock, days=30, minutes=1)
    push(home)
    assert len(drafted(conn, home)) == 0
    push(home)
    assert len(drafted(conn, home)) == 1


def test_the_configured_assignee_gets_it_when_they_can_run_runners(home, conn):
    home['app'].config['CENTRAL_REFLECTION_ASSIGNEE'] = 'DEV@example.com'
    push(home, 3)
    [task] = drafted(conn, home)
    dev = home['accounts']['dev']
    assert task['assignee_id'] == dev['id'] and task['created_by'] == dev['id']
    assert conn.query_one('SELECT account_id FROM notification')['account_id'] == dev['id']


@pytest.mark.parametrize('email', ['outsider@example.com', 'viewer@example.com', 'nobody@example.com'])
def test_otherwise_the_first_owner_gets_it(home, conn, email):
    home['app'].config['CENTRAL_REFLECTION_ASSIGNEE'] = email
    push(home, 3)
    assert drafted(conn, home)[0]['assignee_id'] == home['accounts']['owner']['id']


def test_a_plan_sent_back_can_draft(home, conn):
    home['app'].config['CENTRAL_REFLECTION_MIN_COUNT'] = '1'
    owner = home['accounts']['owner']
    projects.add_member(conn, home['alpha']['id'], 'owner@example.com', 'owner', '2026-10-31T09:00:00Z')
    conn.commit()
    row = tasks.create(conn, home['alpha'], owner, owner, 'CSV export', 'Export.', START, status='plan_review',
                       plan='## Intent\nCSV.', items='- [ ] E1')
    client = signed_in(home['app'], 'owner@example.com')
    resp = post(client, '/projects/alpha/tasks/%d/decide' % row['number'], decision='changes', comment='Smaller.')
    assert resp.status_code in (200, 302, 303)
    [task] = drafted(conn, home)
    assert task['title'] == 'Runner friction: plan_changes' and 'planner prompt' in task['description']


def test_a_failure_while_drafting_does_not_fail_the_push(home, conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('broken')
    monkeypatch.setattr(frictions, 'draft_due', boom)
    push(home, 3)
    assert conn.query_one('SELECT count FROM friction_count')['count'] == 3


def test_settings_defaults_and_bad_values():
    assert config.reflection({}) == {'project': None, 'assignee': None, 'min_count': 3, 'min_projects': 2, 'daily': 3}
    got = config.reflection({'CENTRAL_REFLECTION_PROJECT': ' auto-runner ', 'CENTRAL_REFLECTION_MIN_COUNT': 'x',
                             'CENTRAL_REFLECTION_MIN_PROJECTS': '0', 'CENTRAL_REFLECTION_DAILY': '5'})
    assert got == {'project': 'auto-runner', 'assignee': None, 'min_count': 3, 'min_projects': 2, 'daily': 5}
    assert 'CENTRAL_REFLECTION_PROJECT' in config.ENV_VARS


def test_the_page_says_when_drafting_is_off(home, conn):
    admin = accounts.create(conn, 'admin@example.com', PW, role='admin')
    conn.commit()
    client = signed_in(home['app'], admin['email'])
    assert 'Drafting is on' in client.get('/frictions').get_data(as_text=True)
    home['app'].config['CENTRAL_REFLECTION_PROJECT'] = None
    assert 'Drafting is off: set CENTRAL_REFLECTION_PROJECT' in client.get('/frictions').get_data(as_text=True)
