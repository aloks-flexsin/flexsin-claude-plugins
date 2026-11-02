"""auto-runner plugin: the runner sends its event ledger to the central service (autodeliver.push), so blockers can be seen
and answered there. Every request goes through the real app's Flask test client: the HTTP binding is real, no socket is opened."""
import json
import re
import sys
import uuid

import pytest

from tests.auto_runner.test_runner import BLOCKER, ar, blocking_agent, events, picking_agent, project, \
    project_statuses, set_cfg  # noqa: F401  (project is a fixture)

ad = ar.autodeliver
ab = ar.autoblock
ENV = ar.autosafe.CENTRAL_TOKEN_ENV
BASE = 'https://central.test'
SLUG = 'acme-web'
PW = 'violet-anchor-58-tide'
AT = '2026-10-31T09:00:00Z'


class Central:
    """The real central app on SQLite, a project `acme-web` with an owner, and a token with both scopes."""

    def __init__(self, root):
        auto_runner = str(ar.HERE.parent)
        if auto_runner not in sys.path:
            sys.path.insert(0, auto_runner)
        import central
        from central import accounts, db, projects, tokens
        url = 'sqlite:///' + (root / 'central.sqlite3').as_posix()
        db.init_db(url)
        self.app = central.create_app({'TESTING': True, 'DATABASE_URL': url})
        self.conn = db.connect(url)
        self.client = self.app.test_client()
        self.project = projects.create(self.conn, SLUG, 'Acme web', AT, 'https://github.com/example/repo.git')
        accounts.create(self.conn, 'owner@example.com', PW)
        projects.add_member(self.conn, self.project['id'], 'owner@example.com', 'owner', AT)
        self.token, self.token_row = tokens.create(self.conn, self.project['id'], ['ingest', 'pull'], None, AT)
        self.calls, self.down = [], False

    def send(self, method, url, headers, body, timeout):
        self.calls.append((method, url))
        if self.down:
            raise ConnectionRefusedError('no route')
        assert url.startswith(BASE + '/')
        r = self.client.open(url[len(BASE):], method=method, headers=headers, data=body, base_url=BASE)
        return r.status_code, r.get_data()

    def transport(self, token=None):
        return ad.HttpTransport(BASE, token or self.token, send=self.send)

    def stored_ids(self):
        return {r['id'] for r in self.conn.query('SELECT id FROM event WHERE project_id = ?', (self.project['id'],))}

    def blocker(self, blocker_id):
        return self.conn.query_one('SELECT * FROM blocker WHERE project_id = ? AND blocker_id = ?',
                                   (self.project['id'], blocker_id))

    def signed_in(self):
        c = self.app.test_client()
        c.environ_base['wsgi.url_scheme'] = 'https'
        token = lambda path: re.search(r'name="csrf_token" value="([^"]+)"',  # noqa: E731
                                       c.get(path).get_data(as_text=True)).group(1)
        assert c.post('/signin', data={'email': 'owner@example.com', 'password': PW,
                                       'csrf_token': token('/signin')}).status_code == 302
        c.csrf = token('/')
        return c


@pytest.fixture
def central(tmp_path):
    c = Central(tmp_path)
    yield c
    c.conn.close()


def ledger(tmp_path, project=SLUG):
    return ar.autoevents.Ledger(tmp_path / '.auto', '20261102-0900', project)


def cursor(tmp_path):
    return json.loads((tmp_path / '.auto' / ad.PUSHED).read_text(encoding='utf-8'))


def test_push_sends_new_lines_once_and_keeps_a_cursor(central, tmp_path):
    led = ledger(tmp_path)
    first = [led.emit('run_start', detail={'branch': 'b'}), led.emit('item_state', 'A1', {'from': 'queued', 'to': 'running'})]
    assert ad.push(central.transport(), tmp_path / '.auto', SLUG) == {'sent': 2, 'skipped': [], 'error': None}
    assert central.stored_ids() == {e['id'] for e in first} and cursor(tmp_path)['lines'] == 2

    assert ad.push(central.transport(), tmp_path / '.auto', SLUG)['sent'] == 0     # nothing new: no request at all
    assert [c for c in central.calls if c[0] == 'POST'] == [('POST', BASE + '/api/v1/events')]

    later = led.emit('run_end', detail={'exit': 0, 'reason': 'max_iterations'})
    assert ad.push(central.transport(), tmp_path / '.auto', SLUG)['sent'] == 1
    assert later['id'] in central.stored_ids() and cursor(tmp_path)['lines'] == 3


def test_push_without_a_ledger_sends_nothing(central, tmp_path):
    assert ad.push(central.transport(), tmp_path / '.auto', SLUG) == {'sent': 0, 'skipped': [], 'error': None}
    assert central.calls == []


def test_push_sends_only_this_projects_events(central, tmp_path):
    ledger(tmp_path, 'other-folder-name').emit('run_start')
    mine = ledger(tmp_path).emit('run_start')
    assert ad.push(central.transport(), tmp_path / '.auto', SLUG)['sent'] == 1
    assert central.stored_ids() == {mine['id']} and cursor(tmp_path)['lines'] == 2


def test_an_outage_keeps_the_cursor_and_the_next_push_catches_up(central, tmp_path):
    e = ledger(tmp_path).emit('run_start')
    central.down = True
    out = ad.push(central.transport(), tmp_path / '.auto', SLUG)
    assert out['sent'] == 0 and 'ConnectionRefusedError' in out['error'] and cursor(tmp_path)['lines'] == 0
    central.down = False
    assert ad.push(central.transport(), tmp_path / '.auto', SLUG)['sent'] == 1 and e['id'] in central.stored_ids()


def test_a_refused_token_stops_without_moving_the_cursor(central, tmp_path):
    ledger(tmp_path).emit('run_start')
    out = ad.push(central.transport(token='art_' + 'x' * 43), tmp_path / '.auto', SLUG)
    assert out['sent'] == 0 and '401' in out['error'] and cursor(tmp_path)['lines'] == 0
    assert central.stored_ids() == set()


def test_an_event_the_service_will_never_take_is_skipped_and_the_rest_arrive(central, tmp_path):
    led = ledger(tmp_path)
    before = led.emit('run_start')
    bad = dict(before, id=uuid.uuid4().hex, type='not-a-type')
    with (tmp_path / '.auto' / 'events.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(bad) + '\n')
    after = led.emit('run_end', detail={'exit': 0, 'reason': 'done'})
    out = ad.push(central.transport(), tmp_path / '.auto', SLUG)
    assert out == {'sent': 2, 'skipped': [bad['id']], 'error': None}
    assert central.stored_ids() == {before['id'], after['id']}
    assert cursor(tmp_path)['lines'] == 3 and cursor(tmp_path)['skipped'] == [bad['id']]


def test_a_half_written_last_line_waits_for_the_next_push(central, tmp_path):
    e = ledger(tmp_path).emit('run_start')
    with (tmp_path / '.auto' / 'events.jsonl').open('a', encoding='utf-8') as f:
        f.write('{"schema": "event.v1", "id": "half')
    assert ad.push(central.transport(), tmp_path / '.auto', SLUG)['sent'] == 1
    assert cursor(tmp_path)['lines'] == 1 and central.stored_ids() == {e['id']}


def test_large_ledgers_go_in_several_batches(central, tmp_path, monkeypatch):
    monkeypatch.setattr(ad, 'MAX_PUSH_EVENTS', 3)
    led = ledger(tmp_path)
    sent = [led.emit('item_state', 'A1', {'from': 'queued', 'to': f's{i}'}) for i in range(7)]
    assert ad.push(central.transport(), tmp_path / '.auto', SLUG)['sent'] == 7
    assert len([c for c in central.calls if c[0] == 'POST']) == 3
    assert central.stored_ids() == {e['id'] for e in sent}


def test_a_raised_event_carries_the_content_a_person_needs():
    b = ab.new_blocker(**BLOCKER, project=SLUG, task='A1', run='r', agent_role='implementer', stage='A1')
    content = ab.content(b)
    assert set(content) == set(ab.CONTENT_FIELDS)
    assert content['question'] == BLOCKER['question'] and content['options'] == BLOCKER['options']
    assert not {'history', 'idempotency_key', 'state', 'run'} & set(content)


# --- a whole round trip: the runner raises, a person answers on the page, the runner resumes ---------------------------

def test_a_blocker_raised_in_a_run_is_answered_on_the_page_and_the_item_resumes(project, central, monkeypatch, capsys):
    monkeypatch.setenv(ENV, central.token)
    monkeypatch.setattr(ad, 'urllib_send', central.send)
    set_cfg(project, central={'enabled': True, 'url': BASE, 'project': SLUG})

    assert ar.run(project, max_iterations=4, claude=blocking_agent('A1', BLOCKER)) == 0
    b = ab.Store(project / '.auto').all()[0][0]
    assert project_statuses(project)['A1'][0] == 'blocked'
    row = central.blocker(b['id'])
    assert row is not None and row['status'] == 'OPEN' and row['question'] == BLOCKER['question']
    assert central.stored_ids() == {e['id'] for e in events(project)}        # the whole ledger, run_end included

    owner = central.signed_in()
    page = owner.get(f'/projects/{SLUG}/blockers/{b["id"]}').get_data(as_text=True)
    assert BLOCKER['question'] in page and 'Matches the brand.' in page
    r = owner.post(f'/projects/{SLUG}/blockers/{b["id"]}/answer',
                   data={'option': 'Orange', 'text': 'The brand orange.', 'csrf_token': owner.csrf})
    assert r.status_code == 302 and central.blocker(b['id'])['status'] == 'PENDING_DELIVERY'

    calls = []
    assert ar.run(project, max_iterations=2, claude=picking_agent(calls), allow_dirty=True) == 0
    assert calls == ['A1'] and project_statuses(project)['A1'][0] == 'done'
    assert ab.Store(project / '.auto').load(b['id'])['state'] == 'RESUMED'
    assert central.conn.query_one('SELECT status FROM delivery')['status'] == 'acknowledged'
    assert central.blocker(b['id'])['status'] == 'RESUMED'
    out = capsys.readouterr()
    assert central.token not in out.out + out.err and 'events not sent' not in out.out


def test_pushes_stay_small_and_wait_longer_than_pulls(central, tmp_path):
    led = ledger(tmp_path)
    for i in range(45):
        led.emit('item_state', 'A1', {'from': 'queued', 'to': f's{i}'})
    seen = []

    def timed(method, url, headers, body, timeout):
        seen.append((method, len(json.loads(body)) if body else 0, timeout))
        return central.send(method, url, headers, body, timeout)

    t = ad.HttpTransport(BASE, central.token, send=timed)
    assert ad.push(t, tmp_path / '.auto', SLUG)['sent'] == 45
    assert [n for _, n, _ in seen] == [20, 20, 5]
    assert {timeout for _, _, timeout in seen} == {ad.PUSH_TIMEOUT}
    seen.clear()
    t.pull_pending(SLUG)
    assert seen == [('GET', 0, 10)]
