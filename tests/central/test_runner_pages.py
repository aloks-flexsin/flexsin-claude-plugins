"""Central service: runners on Overview, Runs and My work, with Pause, Resume and Stop for the runner's person and admins
(confirmed and audited)."""
import datetime

from central import audit, runners

from .test_access import post
from .test_repo_url import conn, world  # noqa: F401  (fixtures)
from .test_runners import LABEL, beat
from .test_tasks import START, clock, team  # noqa: F401  (fixtures)

PAGES = ('/projects/alpha/tasks', '/projects/alpha/runs')


def runner(conn, team):
    return runners.get(conn, team['alpha']['id'], LABEL)


def control_url(row, action):
    return '/projects/alpha/runners/%s/%s' % (row['id'], action)


def page(team, who, path):
    resp = team['clients'][who].get(path)
    assert resp.status_code == 200, resp.get_data(as_text=True)[-500:]
    return resp.get_data(as_text=True)


def test_runners_show_on_the_board_and_runs_with_their_state_in_words(app, team, conn, clock):
    assert beat(app, team, state='building #3', task=3, spend_usd=1.5).status_code == 200
    for path in PAGES:
        text = page(team, 'viewer', path)
        assert LABEL in text and 'Building · task #3' in text and 'spent today $1.50' in text
    clock.now = START + datetime.timedelta(minutes=10)
    beat(app, team, state='watching')
    assert 'Watching for work' in page(team, 'owner', '/projects/alpha/tasks')
    beat(app, team, state='paused')
    assert 'Paused' in page(team, 'owner', '/projects/alpha/runs')
    conn.execute("UPDATE runner SET beat_at = '2026-11-23T07:00:00Z'")   # no beat for 2 hours: silent wins over "paused"
    conn.commit()
    text = page(team, 'owner', '/projects/alpha/runs')
    assert 'Silent' in text and 'no beat for 2 hours or more' in text


def test_who_sees_which_buttons(app, team, conn):
    beat(app, team)                                                  # dev's runner
    row = runner(conn, team)
    for who in ('dev', 'admin'):                                     # the runner's person and admins
        for path in PAGES:
            text = page(team, who, path)
            assert control_url(row, 'pause') in text and control_url(row, 'stop') in text, (who, path)
            assert control_url(row, 'resume') not in text
    for who in ('owner', 'manager', 'viewer'):                       # other members see it without control buttons
        for path in PAGES:
            text = page(team, who, path)
            assert LABEL in text and all(control_url(row, a) not in text for a in ('pause', 'stop', 'resume')), (who, path)
            # managers and above may give the runner to someone else (task pool); viewers may not
            assert ('/runners/%s/hand-over' % row['id'] in text) == (who != 'viewer'), (who, path)
    assert LABEL in page(team, 'dev', '/my') and control_url(row, 'pause') in page(team, 'dev', '/my')
    assert LABEL in page(team, 'admin', '/my')
    assert LABEL not in page(team, 'owner', '/my')                   # My work lists only runners you may control


def test_others_cannot_control_a_runner(app, team, conn):
    beat(app, team)
    row = runner(conn, team)
    for who in ('owner', 'manager', 'viewer'):
        for action in ('pause', 'stop', 'resume'):
            assert post(team['clients'][who], control_url(row, action), confirm='1').status_code == 404
    assert post(team['clients']['dev'], '/projects/alpha/runners/%s/explode' % row['id'], confirm='1').status_code == 404
    assert post(team['clients']['dev'], '/projects/alpha/runners/nope/pause', confirm='1').status_code == 404
    assert runner(conn, team)['control'] is None
    assert [r for r in audit.rows(conn) if r['action'].startswith('runner.') and r['action'] != 'runner.start'] == []


def test_nothing_changes_without_confirmation(app, team, conn):
    beat(app, team)
    row = runner(conn, team)
    for action, question in (('pause', 'Pause %s?' % LABEL), ('stop', 'Stop %s?' % LABEL), ('resume', 'Resume %s?' % LABEL)):
        resp = post(team['clients']['dev'], control_url(row, action), next='/projects/alpha/tasks')
        text = resp.get_data(as_text=True)
        assert resp.status_code == 200 and question in text and 'name="confirm" value="1"' in text
        assert 'name="next" value="/projects/alpha/tasks"' in text and 'Keep it as it is' in text
    assert runner(conn, team)['control'] is None
    assert audit.rows(conn, 'runner.pause') == [] and audit.rows(conn, 'runner.stop') == []
    text = page(team, 'dev', '/projects/alpha/tasks')                      # with JavaScript the dialog asks first
    assert 'data-confirm="Pause %s?"' % LABEL in text and 'data-confirm-action="Stop runner"' in text


def test_pause_resume_and_stop_reach_the_runner_and_are_audited(app, team, conn, clock):
    beat(app, team)
    row = runner(conn, team)
    dev, admin = team['clients']['dev'], team['clients']['admin']

    resp = post(dev, control_url(row, 'pause'), confirm='1', next='/projects/alpha/tasks')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/projects/alpha/tasks')
    assert beat(app, team).get_json()['control'] == 'pause'
    text = page(team, 'dev', '/projects/alpha/tasks')
    assert 'Asked to pause' in text and control_url(row, 'resume') in text and control_url(row, 'pause') not in text

    assert post(admin, control_url(row, 'resume'), confirm='1').status_code == 302
    assert beat(app, team).get_json()['control'] is None
    assert post(admin, control_url(row, 'stop'), confirm='1', next='https://evil.example/').status_code == 302
    assert beat(app, team).get_json()['control'] == 'stop'
    text = page(team, 'admin', '/projects/alpha/runs')
    assert 'Asked to stop' in text and control_url(row, 'stop') not in text and control_url(row, 'resume') in text
    assert post(admin, control_url(row, 'stop'), confirm='1').status_code == 302   # already stopped: no second row

    rows = [r for r in audit.rows(conn) if r['action'] in ('runner.pause', 'runner.resume', 'runner.stop')]
    assert [(r['action'], r['account_id']) for r in rows] == [
        ('runner.pause', team['accounts']['dev']['id']), ('runner.resume', team['accounts']['admin']['id']),
        ('runner.stop', team['accounts']['admin']['id'])]
    assert all(r['project_id'] == team['alpha']['id'] and r['detail']['label'] == LABEL
               and r['detail']['owner'] == 'dev@example.com' for r in rows)
    stored = runner(conn, team)
    assert (stored['control'], stored['control_by']) == ('stop', team['accounts']['admin']['id'])
    assert 'stopped the runner' in page(team, 'owner', '/projects/alpha/tasks')      # Recent activity says it in words


def test_a_runner_of_another_project_is_not_reachable(app, team, conn):
    beat(app, team)
    row = runner(conn, team)
    resp = post(team['clients']['dev'], '/projects/beta/runners/%s/pause' % row['id'], confirm='1')
    assert resp.status_code == 404 and runner(conn, team)['control'] is None


def test_runs_page_shows_paused_by_supervisor(app, team, conn):
    from central import accounts, personal
    from .test_tasks import PW
    beat(app, team)
    row = runner(conn, team)
    sup = accounts.create(conn, 'sup@example.com', PW, role='supervisor')
    token = personal.create_token(conn, sup['id'], 'sup', START)
    url = '/api/v1/supervisor/runners/%s/pause?project=alpha' % row['id']
    assert team['clients']['viewer'].post(url, headers={'Authorization': 'Bearer ' + token}).status_code == 200
    for path in PAGES:
        text = page(team, 'viewer', path)
        assert 'Paused by the Supervisor' in text and 'Paused by a person' not in text
    assert runners.can_control(runner(conn, team), sup) is True
    post(team['clients']['dev'], control_url(row, 'resume'), confirm='1')                    # a person may still resume
    assert runner(conn, team)['control'] is None
    post(team['clients']['dev'], control_url(row, 'pause'), confirm='1')
    assert 'Paused by the Supervisor' not in page(team, 'viewer', '/projects/alpha/runs')    # a person's pause is theirs
