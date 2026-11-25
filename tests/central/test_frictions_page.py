"""Central service: the admins' Runner frictions page, with Mute and Unmute (decision record 0011, R7)."""
import datetime
import json
import re

import pytest

from central import accounts, audit, frictions, projects

from .test_access import PW, new_client, post, signed_in
from .test_ingest import START, clock, conn, friction, send, world  # noqa: F401  (fixtures)

PAGE = '/frictions'


@pytest.fixture
def site(world, conn, clock):
    """Two frictions in alpha, one also in beta, seen at different times; an admin and a plain member."""
    accounts.create(conn, 'admin@example.com', PW, role='admin')
    accounts.create(conn, 'dev@example.com', PW)
    projects.add_member(conn, world['alpha']['id'], 'dev@example.com', 'owner', '2026-10-31T09:00:00Z')
    conn.commit()
    for minutes in (0, 5, 10):
        clock.now = START + datetime.timedelta(minutes=minutes)
        send(world['app'], world['token'], friction())                                  # retry:exit_1 x3
    clock.now = START + datetime.timedelta(hours=1)
    send(world['app'], world['token'], friction(kind='permission_denied', subject='Bash:sed'))
    send(world['app'], world['beta_token'], friction(item=None, kind='permission_denied', subject='Bash:sed')
         | {'project': 'beta'})
    return dict(world, admin=signed_in(world['app'], 'admin@example.com'), dev=signed_in(world['app'], 'dev@example.com'))


def sig_row(conn, sig):
    return conn.query_one('SELECT * FROM friction_signature WHERE signature = ?', (sig,))


def detail(row):
    return json.loads(row['detail']) if isinstance(row['detail'], str) else row['detail']


def test_an_admin_sees_rows_with_counts_and_states(site, conn):
    resp = site['admin'].get(PAGE)
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    perm, retry = page.index('permission_denied:Bash:sed'), page.index('retry:exit_1')
    assert perm < retry                                     # newest last-seen first
    assert '<strong>3</strong> times' in page and '<strong>2</strong> times' in page
    assert 'title="Alpha, Beta">2 projects' in page and 'title="Alpha">1 project<' in page
    assert page.count('Watching') >= 2 and '<time datetime=' in page
    rows = frictions.overview(conn)
    assert [(r['signature'], r['count'], r['projects']) for r in rows] == [
        ('permission_denied:Bash:sed', 2, ['Alpha', 'Beta']), ('retry:exit_1', 3, ['Alpha'])]
    assert rows[1]['first_seen'] == '2026-10-31T09:00:00Z' and rows[1]['last_seen'] == '2026-10-31T09:10:00Z'


def test_accounts_links_to_it(site):
    page = site['admin'].get('/admin/accounts').get_data(as_text=True)
    assert 'href="/frictions">Runner frictions</a>' in page


@pytest.mark.parametrize('who', ['dev', 'anonymous'])
def test_others_get_404(site, conn, who):
    client = new_client(site['app']) if who == 'anonymous' else site[who]
    assert client.get(PAGE).status_code == 404
    assert post(client, '/frictions/mute', signature='retry:exit_1', reason='x', confirm='1').status_code == 404
    assert sig_row(conn, 'retry:exit_1')['state'] == 'watching'
    assert audit.rows(conn, 'friction.mute') == []


def test_mute_and_unmute_change_the_state_and_are_audited(site, conn, clock):
    clock.now = START + datetime.timedelta(days=1)
    admin = signed_in(site['app'], 'admin@example.com')     # a fresh session at the moved clock
    resp = post(admin, '/frictions/mute', signature='retry:exit_1', reason='  Known flaky test  ', confirm='1')
    assert resp.status_code == 302
    row = sig_row(conn, 'retry:exit_1')
    admin_id = accounts.find_by_email(conn, 'admin@example.com')['id']
    assert (row['state'], row['reason'], row['changed_by'], row['changed_at']) == (
        'muted', 'Known flaky test', admin_id, '2026-11-01T09:00:00Z')
    [a] = audit.rows(conn, 'friction.mute')
    assert detail(a) == {'signature': 'retry:exit_1', 'from': 'watching', 'reason': 'Known flaky test'}
    assert a['account_id'] == admin_id
    page = admin.get(PAGE + '?state=muted').get_data(as_text=True)
    assert 'Muted: Known flaky test' in page and 'Unmute' in page

    clock.now = START + datetime.timedelta(days=2)
    admin = signed_in(site['app'], 'admin@example.com')
    assert post(admin, '/frictions/unmute', signature='retry:exit_1', confirm='1').status_code == 302
    row = sig_row(conn, 'retry:exit_1')
    assert (row['state'], row['reason'], row['changed_at']) == ('watching', None, '2026-11-02T09:00:00Z')
    [u] = audit.rows(conn, 'friction.unmute')
    assert detail(u) == {'signature': 'retry:exit_1', 'reason_was': 'Known flaky test'}


def test_mute_without_confirm_renders_the_confirm_page(site, conn):
    resp = post(site['admin'], '/frictions/mute', signature='retry:exit_1', reason='Known')
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200 and 'Mute retry:exit_1?' in page
    assert re.search(r'name="reason" value="Known"', page) and 'name="confirm" value="1"' in page
    assert sig_row(conn, 'retry:exit_1')['state'] == 'watching' and audit.rows(conn, 'friction.mute') == []


def test_mute_needs_a_reason(site, conn):
    resp = post(site['admin'], '/frictions/mute', signature='retry:exit_1', reason='  ', confirm='1')
    assert resp.status_code == 400 and 'Say why it is muted.' in resp.get_data(as_text=True)
    assert sig_row(conn, 'retry:exit_1')['state'] == 'watching'
    assert len(audit.rows(conn, 'friction.mute.refused')) == 1


def test_unmuting_a_row_that_is_not_muted_is_refused(site, conn):
    resp = post(site['admin'], '/frictions/unmute', signature='retry:exit_1', confirm='1')
    assert resp.status_code == 400 and sig_row(conn, 'retry:exit_1')['changed_at'] is None


def test_an_unknown_signature_is_404(site):
    assert post(site['admin'], '/frictions/mute', signature='nope', reason='x', confirm='1').status_code == 404


def test_a_post_without_the_csrf_token_is_refused(site, conn):
    resp = site['admin'].post('/frictions/mute', data={'signature': 'retry:exit_1', 'reason': 'x', 'confirm': '1'})
    assert resp.status_code in (400, 403) and sig_row(conn, 'retry:exit_1')['state'] == 'watching'


def test_filters_by_kind_and_state(site, conn):
    def shown(query):
        page = site['admin'].get(PAGE + query).get_data(as_text=True)
        return [s for s in ('retry:exit_1', 'permission_denied:Bash:sed') if '>%s</code>' % s in page]
    assert shown('') == ['retry:exit_1', 'permission_denied:Bash:sed']
    assert shown('?kind=retry') == ['retry:exit_1']
    assert shown('?kind=permission_denied&state=watching') == ['permission_denied:Bash:sed']
    assert shown('?state=muted') == []
    conn.execute("UPDATE friction_signature SET state = 'muted', reason = 'x' WHERE signature = 'retry:exit_1'")
    conn.commit()
    assert shown('?state=muted') == ['retry:exit_1']
    assert 'No runner friction matches.' in site['admin'].get(PAGE + '?kind=gate_stop').get_data(as_text=True)


def test_a_drafted_row_links_its_task(site, conn):
    conn.execute("UPDATE friction_signature SET state = 'drafted', task_project_id = ?, task_number = 7"
                 " WHERE signature = 'retry:exit_1'", (site['alpha']['id'],))
    conn.commit()
    page = site['admin'].get(PAGE).get_data(as_text=True)
    assert 'href="/projects/alpha/tasks/7">Task #7</a>' in page and 'Drafted' in page


def test_an_empty_page_says_so(world, conn):
    accounts.create(conn, 'admin@example.com', PW, role='admin')
    conn.commit()
    page = signed_in(world['app'], 'admin@example.com').get(PAGE).get_data(as_text=True)
    assert 'No runner frictions yet.' in page
