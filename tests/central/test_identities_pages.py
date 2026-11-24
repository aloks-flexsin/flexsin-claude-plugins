"""Central service: Claude accounts on the Account page and on Runs (slice 1). Events are posted through
`POST /api/v1/events` with a personal token, then both pages are read as the signed-in person."""
import datetime
import json

from central import accounts, personal, projects, runs

from .test_access import PW, signed_in
from .test_ingest import clock, conn, world  # noqa: F401  (fixtures)
from .test_runs import ev

AT = '2026-10-31T09:00:00Z'
NOW = datetime.datetime(2026, 11, 23, 12, 0, tzinfo=datetime.timezone.utc)


def _post(app, raw, events, query=''):
    c = app.test_client()
    c.environ_base['wsgi.url_scheme'] = 'https'
    return c.post('/api/v1/events' + query, data=json.dumps(events),
                  headers={'Authorization': 'Bearer ' + raw, 'Content-Type': 'application/json'})


def _alok(world, conn):
    acct = accounts.create(conn, 'alok@example.com', PW)
    accounts.set_name(conn, acct['id'], 'Alok Sharma')
    conn.commit()
    projects.add_member(conn, world['alpha']['id'], 'alok@example.com', 'developer', AT)
    return personal.create_token(conn, acct['id'], 'laptop', datetime.datetime(2026, 11, 23, 9, 0, tzinfo=datetime.timezone.utc))


def test_account_page_says_none_reported_yet(world, conn):
    _alok(world, conn)
    page = signed_in(world['app'], 'alok@example.com').get('/account').get_data(as_text=True)
    assert 'Your Claude accounts' in page
    assert 'None reported yet: your runners report it when they start' in page


def test_account_and_runs_show_the_reported_pairs(world, conn, clock):
    clock.now = NOW
    raw = _alok(world, conn)
    sent = [ev('r1', 'run_start', '2026-11-23T10:00:00+00:00', user='alok', account='bhawish', branch='b1'),
            ev('r1', 'run_end', '2026-11-23T10:30:00+00:00', exit=0, reason='queue_empty'),
            ev('r2', 'run_start', '2026-11-23T11:00:00+00:00', user='alok', account='alok-pers', branch='b2'),
            ev('r2', 'run_end', '2026-11-23T11:30:00+00:00', exit=0, reason='queue_empty')]
    assert _post(world['app'], raw, sent, '?project=alpha').status_code == 200
    client = signed_in(world['app'], 'alok@example.com')

    account = client.get('/account').get_data(as_text=True)
    assert 'None reported yet' not in account
    for pair in (('alok', 'bhawish'), ('alok', 'alok-pers')):
        assert '<td>%s</td><td>%s</td><td>auto-runner</td>' % pair in account

    page = client.get('/projects/alpha/runs').get_data(as_text=True)
    assert 'Alok Sharma' in page
    assert 'alok · Claude account bhawish' in page
    assert 'alok · Claude account alok-pers' in page


def test_runs_marks_only_a_mismatched_account_check(world, conn, clock):
    clock.now = NOW
    raw = _alok(world, conn)
    sent = []
    for n, check in enumerate(('mismatch', 'match', None, 'bogus', ['x'])):
        extra = {} if check is None else {'account_check': check}
        sent.append(ev('r%d' % n, 'run_start', '2026-11-23T10:0%d:00+00:00' % n, user='alok', account='acc%d' % n,
                       branch='b%d' % n, **extra))
    assert _post(world['app'], raw, sent, '?project=alpha').status_code == 200
    page = signed_in(world['app'], 'alok@example.com').get('/projects/alpha/runs').get_data(as_text=True)
    assert page.count('Claude account not confirmed') == 1     # only the mismatched run
    by_run = {r['run']: r['account_check'] for r in runs.summarise(sent, NOW)}
    assert by_run == {'r0': 'mismatch', 'r1': 'match', 'r2': None, 'r3': None, 'r4': None}


def test_a_project_token_run_keeps_the_old_who_text(world, conn, clock):
    clock.now = NOW
    sent = [ev('r1', 'run_start', '2026-11-23T10:00:00+00:00', user='ravi', account='bhawish', branch='b1'),
            ev('r1', 'run_end', '2026-11-23T10:30:00+00:00', exit=0, reason='queue_empty')]
    assert _post(world['app'], world['token'], sent).status_code == 200
    _alok(world, conn)
    page = signed_in(world['app'], 'alok@example.com').get('/projects/alpha/runs').get_data(as_text=True)
    assert 'bhawish account' in page and 'Claude account' not in page


def test_summarise_keeps_user_account_and_the_signed_in_name_apart():
    events = [dict(ev('r1', 'run_start', '2026-11-23T10:00:00+00:00', user='alok', account='bhawish'),
                   _signed_in='alok@example.com', _signed_in_name='Alok Sharma')]
    [r] = runs.summarise(events, NOW)
    assert (r['user'], r['account'], r['signed_in'], r['signed_in_name']) == (
        'alok', 'bhawish', 'alok@example.com', 'Alok Sharma')
    assert r['who'] == 'alok via bhawish-claude-auto-runner'


# Slice 2: the team's Claude accounts on Members, and the Runs filter

def _person(world, conn, email, name):
    acct = accounts.create(conn, email, PW)
    accounts.set_name(conn, acct['id'], name)
    conn.commit()
    projects.add_member(conn, world['alpha']['id'], email, 'developer', AT)
    return personal.create_token(conn, acct['id'], 'laptop', datetime.datetime(2026, 11, 23, 9, 0, tzinfo=datetime.timezone.utc))


def _team(world, conn, clock, username_priya='priya'):
    clock.now = NOW
    alok, priya = _alok(world, conn), _person(world, conn, 'priya@example.com', 'Priya Rao')
    run = lambda rid, user, acct, hour, reason='queue_empty', exit_=0: [
        ev(rid, 'run_start', '2026-11-23T%02d:00:00+00:00' % hour, user=user, account=acct, branch='b' + rid),
        ev(rid, 'run_end', '2026-11-23T%02d:30:00+00:00' % hour, exit=exit_, reason=reason)]
    assert _post(world['app'], alok, run('r1', 'alok', 'bhawish', 9), '?project=alpha').status_code == 200
    assert _post(world['app'], alok, run('r2', 'alok', 'bhawish', 10, 'budget', 1), '?project=alpha').status_code == 200
    assert _post(world['app'], priya, run('r3', username_priya, 'bhawish', 11), '?project=alpha').status_code == 200
    assert _post(world['app'], world['token'], run('r4', 'ravi', 'other', 8)).status_code == 200


def test_members_page_marks_a_shared_claude_account(world, conn, clock):
    _team(world, conn, clock)
    page = signed_in(world['app'], 'alok@example.com').get('/projects/alpha/settings/members').get_data(as_text=True)
    assert page.count('<code>bhawish</code> <span class="chip tone-attention">shared</span> '
                      '<span class="surface">(auto-runner)</span>') == 2
    assert 'is reported by more than one member' not in page


def test_surface_shows_on_account_and_members_and_sharing_spans_surfaces(world, conn, clock):
    from central import identities
    _team(world, conn, clock)
    priya = accounts.find_by_email(conn, 'priya@example.com')
    identities.record(conn, priya['id'], 'priya', 'bhawish', AT, 'cli')
    conn.commit()
    client = signed_in(world['app'], 'alok@example.com')
    assert '<td>auto-runner</td>' in client.get('/account').get_data(as_text=True)
    page = client.get('/projects/alpha/settings/members').get_data(as_text=True)
    assert '(auto-runner)' in page and '(cli)' in page
    assert page.count('shared</span>') >= 3


def test_members_page_warns_when_a_username_is_reported_twice(world, conn, clock):
    _team(world, conn, clock, username_priya='alok')
    page = signed_in(world['app'], 'priya@example.com').get('/projects/alpha/settings/members').get_data(as_text=True)
    assert 'the username <code>alok</code> is reported by more than one member' in page


def test_members_page_without_reports_says_so(world, conn):
    _alok(world, conn)
    page = signed_in(world['app'], 'alok@example.com').get('/projects/alpha/settings/members').get_data(as_text=True)
    assert 'no Claude account reported' in page and 'shared' not in page.split('People')[1].split('roles-legend')[0]


def test_runs_filter_by_claude_account(world, conn, clock):
    _team(world, conn, clock)
    client = signed_in(world['app'], 'alok@example.com')
    page = client.get('/projects/alpha/runs?claude=bhawish').get_data(as_text=True)
    assert 'Alok Sharma' in page and 'Priya Rao' in page and 'Claude account other' not in page
    assert 'ravi' not in page
    assert 'aria-label="Claude account"' in page and 'All Claude accounts' in page
    both = client.get('/projects/alpha/runs').get_data(as_text=True)
    assert 'ravi' in both


def test_runs_claude_and_outcome_combine(world, conn, clock):
    _team(world, conn, clock)
    client = signed_in(world['app'], 'alok@example.com')
    page = client.get('/projects/alpha/runs?claude=bhawish&outcome=finished').get_data(as_text=True)
    assert 'Priya Rao' in page and 'Alok Sharma' in page
    outcome_row = page.split('aria-label="Outcome"')[1].split('</nav>')[0]
    assert outcome_row.count('claude=bhawish') == 4                   # All, Stopped, Silent, Finished
    assert 'Finished 2' in outcome_row and 'Stopped 1' in outcome_row    # counts keep the Claude filter
    # the Claude chips keep the outcome
    assert 'outcome=finished' in page.split('aria-label="Claude account"')[1]
    stopped = client.get('/projects/alpha/runs?claude=bhawish&outcome=stopped').get_data(as_text=True)
    assert 'Priya Rao' not in stopped and 'Alok Sharma' in stopped


def test_runs_unknown_claude_value_shows_no_runs(world, conn, clock):
    _team(world, conn, clock)
    r = signed_in(world['app'], 'alok@example.com').get('/projects/alpha/runs?claude=nobody')
    assert r.status_code == 200
    assert 'No run here with that outcome.' in r.get_data(as_text=True)


def test_a_stranger_still_gets_404_on_members_and_runs(world, conn, clock):
    _team(world, conn, clock)
    accounts.create(conn, 'eve@example.com', PW)
    conn.commit()
    client = signed_in(world['app'], 'eve@example.com')
    assert client.get('/projects/alpha/runs?claude=bhawish').status_code == 404
    assert client.get('/projects/alpha/settings/members').status_code == 404
