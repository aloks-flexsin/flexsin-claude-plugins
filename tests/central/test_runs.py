"""Central service: the runs page summarises the events runners send (who, branch, state, items, tests, pull request).
Events are written by the plugin's real `autoevents.Ledger` and posted unchanged through `POST /api/v1/events`."""
import datetime

from central import accounts, projects, runs

from .test_access import PW, new_client, signed_in
from .test_ingest import START, autoevents, clock, conn, send, world  # noqa: F401  (fixtures)

NOW = datetime.datetime(2026, 11, 23, 12, 0, tzinfo=datetime.timezone.utc)


def ev(run, type, time, item=None, **detail):
    return {'schema': 'event.v1', 'id': '%s-%s-%s' % (run, type, time), 'time': time, 'run': run, 'project': 'alpha',
            'type': type, 'item': item, 'detail': detail, 'evidence_class': 'runner_observed'}


def test_a_finished_run_with_a_pull_request():
    events = [
        ev('r1', 'run_start', '2026-11-23T10:00:00+00:00', user='alok', account='bhawish',
           branch='alok/bhawish-claude-auto-runner/feat/csv', base='main'),
        ev('r1', 'item_state', '2026-11-23T10:01:00+00:00', 'A1', **{'from': 'queued', 'to': 'running'}),
        ev('r1', 'tests', '2026-11-23T10:20:00+00:00', 'A1', result='pass', exit=0),
        ev('r1', 'item_state', '2026-11-23T10:21:00+00:00', 'A1', **{'from': 'running', 'to': 'done'}),
        ev('r1', 'publish', '2026-11-23T10:22:00+00:00', pushed=True, pr_url='https://github.com/o/r/pull/9'),
        ev('r1', 'run_end', '2026-11-23T10:22:01+00:00', exit=0, reason='max_iterations'),
    ]
    [r] = runs.summarise(events, NOW)
    assert r['who'] == 'alok via bhawish-claude-auto-runner' and r['state'] == 'finished'
    assert (r['done'], r['current'], r['tests'], r['pr_url']) == (1, None, 'pass', 'https://github.com/o/r/pull/9')
    assert r['branch'] == 'alok/bhawish-claude-auto-runner/feat/csv' and r['branch_short'] == 'feat/csv'
    assert r['pr_label'] == '#9' and r['started_at'] == '2026-11-23 10:00 UTC'


def test_running_stopped_and_silent_runs():
    events = [
        ev('live', 'run_start', '2026-11-23T11:30:00+00:00', user='bhawish', account='bhawish', branch='b'),
        ev('live', 'item_state', '2026-11-23T11:31:00+00:00', 'B2', **{'from': 'queued', 'to': 'running'}),
        ev('red', 'run_start', '2026-11-23T08:00:00+00:00', user='alok', account='alok'),
        ev('red', 'run_end', '2026-11-23T08:30:00+00:00', exit=1, reason='red'),
        ev('gone', 'run_start', '2026-11-23T07:00:00+00:00', user='alok', account='alok'),
        ev('gone', 'item_state', '2026-11-23T07:05:00+00:00', 'C1', **{'from': 'queued', 'to': 'running'}),
    ]
    by = {r['run']: r for r in runs.summarise(events, NOW)}
    assert by['live']['state'] == 'running' and by['live']['current'] == 'B2'
    assert by['red']['state'] == 'stopped' and by['red']['reason'] == 'red'
    assert by['gone']['state'] == 'silent'                              # no end and no event for over 2 hours
    assert [r['run'] for r in runs.summarise(events, NOW)] == ['live', 'red', 'gone']    # newest first


def test_an_old_runner_is_shown_as_unknown_and_a_non_https_link_is_dropped():
    events = [ev('old', 'publish', '2026-11-23T10:00:00+00:00', pushed=True, pr_url='javascript:alert(1)', branch='auto/x')]
    [r] = runs.summarise(events, NOW)
    assert r['who'].startswith('unknown') and r['pr_url'] is None and r['pushed'] is True and r['branch'] == 'auto/x'


def test_two_runs_in_the_same_minute_stay_apart():
    events = [ev('20261123-1000-aaaaaa', 'run_start', '2026-11-23T10:00:00+00:00', user='alok', account='a'),
              ev('20261123-1000-bbbbbb', 'run_start', '2026-11-23T10:00:00+00:00', user='bhawish', account='b')]
    assert {r['who'] for r in runs.summarise(events, NOW)} == {'alok via a-claude-auto-runner',
                                                               'bhawish via b-claude-auto-runner'}


# ---------- the page, fed by the plugin's own ledger ----------

def _member(app, conn, project, role):
    accounts.create(conn, '%s@example.com' % role, PW)
    projects.add_member(conn, project['id'], '%s@example.com' % role, role, '2026-10-31T09:00:00Z')
    return signed_in(app, '%s@example.com' % role)


def test_the_page_shows_a_run_sent_by_the_real_ledger(world, conn, tmp_path):
    ledger = autoevents.Ledger(tmp_path / '.auto', '20261031-0900-abc123', 'alpha')
    sent = [ledger.emit('run_start', detail={'branch': 'alok/bhawish-claude-auto-runner/fix/login', 'base': 'main',
                                             'max_iterations': 3, 'user': 'alok', 'account': 'bhawish'}),
            ledger.emit('item_state', 'A1', {'from': 'queued', 'to': 'running'})]
    assert send(world['app'], world['token'], sent).status_code == 200
    viewer = _member(world['app'], conn, world['alpha'], 'viewer')
    page = viewer.get('/projects/alpha/runs').get_data(as_text=True)
    assert 'class="person-name">alok</span>' in page and 'bhawish account' in page
    assert '>fix/login</code>' in page and 'title="alok/bhawish-claude-auto-runner/fix/login"' in page
    assert 'Working now' in page and 'item A1' in page


def test_strangers_get_404_and_other_projects_runs_never_show(world, conn, tmp_path):
    ledger = autoevents.Ledger(tmp_path / '.auto', 'beta-run', 'beta')
    assert send(world['app'], world['beta_token'], [ledger.emit('run_start', detail={'user': 'zed', 'account': 'z'})]).status_code == 200
    accounts.create(conn, 'stranger@example.com', PW)
    assert signed_in(world['app'], 'stranger@example.com').get('/projects/alpha/runs').status_code == 404
    assert new_client(world['app']).get('/projects/alpha/runs').status_code in (302, 404)
    viewer = _member(world['app'], conn, world['alpha'], 'viewer')
    page = viewer.get('/projects/alpha/runs').get_data(as_text=True)
    assert 'zed' not in page and 'No runs yet' in page
