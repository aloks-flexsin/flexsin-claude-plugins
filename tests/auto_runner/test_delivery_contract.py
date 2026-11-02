"""auto-runner plugin: the delivery contract (auto-runner/docs/delivery-contract.md, roadmap K6).

The same scenarios run against three transports: `LocalTransport` (answers saved in the project's own .auto folder), the
in-memory fake central service (answers made at the centre), and `HttpTransport` talking to the real central app on SQLite
through Flask's test client. No network and no socket.
"""
import json
import secrets
import sys
import uuid

import pytest

from tests.auto_runner.fake_central import FakeCentral
from tests.auto_runner.test_runner import BIN, BLOCKER, ar, blocking_agent, events, picking_agent, project, \
    project_statuses  # noqa: F401  (project is a fixture)

ab = ar.autoblock
ad = ar.autodeliver


class Outage(ad.Transport):
    """Wraps any transport; the call names in `down` raise TransportUnavailable before the inner transport is touched."""

    def __init__(self, inner):
        self.inner, self.down = inner, set()

    def pull_pending(self, project):
        if 'pull' in self.down:
            raise ad.TransportUnavailable('unreachable (simulated)')
        return self.inner.pull_pending(project)

    def ack(self, did):
        if 'ack' in self.down:
            raise ad.TransportUnavailable('unreachable (simulated)')
        return self.inner.ack(did)


class LocalWorld:
    """Answers are saved by `autoblock.py answer` in each project's own .auto folder."""

    def __init__(self, root):
        self.root = root

    def auto(self, project):
        return self.root / project / '.auto'

    def transport(self, project):
        return ad.LocalTransport(self.auto(project))

    def answer(self, project, task, bid, option='Orange'):
        _, d = ab.Store(self.auto(project)).answer(bid, 'pat', 'product owner', option=option)
        return ad.delivery_id(bid, d['version'])

    def row_state(self, project, did):
        row = self.transport(project).row(did)
        return row and row['state']


class CentralWorld:
    """Answers are made at one (fake) central service shared by every project."""

    def __init__(self, root):
        self.root, self.central = root, FakeCentral()

    def auto(self, project):
        return self.root / project / '.auto'

    def transport(self, project):
        return self.central.transport()

    def answer(self, project, task, bid, option='Orange'):
        return self.central.publish(project, task, bid, option=option)

    def row_state(self, project, did):
        return self.central.state(did)


class HttpWorld:
    """Answers are made at the real central service (Flask app on SQLite); the runner holds an `HttpTransport` whose `send`
    goes through the app's test client, so every byte crosses the HTTP binding but no socket is opened."""

    BASE = 'https://central.test'
    AT = '2026-10-31T09:00:00Z'

    def __init__(self, root):
        auto_runner = str(BIN.parent)
        if auto_runner not in sys.path:
            sys.path.insert(0, auto_runner)
        import central
        from central import db
        self.root, self.db = root, db
        url = 'sqlite:///' + (root / 'central.sqlite3').as_posix()
        db.init_db(url)
        self.app = central.create_app({'TESTING': True, 'DATABASE_URL': url})
        self.conn = db.connect(url)
        self.client = self.app.test_client()
        self.projects, self.raised = {}, set()

    def close(self):
        self.conn.close()

    def auto(self, project):
        return self.root / project / '.auto'

    def _project(self, slug):
        from central import projects, tokens
        if slug not in self.projects:
            p = projects.create(self.conn, slug, slug.title(), self.AT, 'https://github.com/example/repo.git')
            owner = secrets.token_hex(16)
            self.conn.execute('INSERT INTO account (id, email, password_hash, role, disabled, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                              (owner, f'owner@{slug}.example.com', 'x', 'user', 0, self.AT))
            self.conn.execute('INSERT INTO member (project_id, account_id, role, created_at) VALUES (?, ?, ?, ?)',
                              (p['id'], owner, 'owner', self.AT))
            self.conn.commit()
            ingest, _ = tokens.create(self.conn, p['id'], ['ingest'], None, self.AT)
            pull, _ = tokens.create(self.conn, p['id'], ['pull'], None, self.AT)
            self.projects[slug] = {'row': p, 'owner': owner, 'ingest': ingest, 'pull': pull}
        return self.projects[slug]

    def send(self, method, url, headers, body, timeout):
        assert url.startswith(self.BASE + '/')
        r = self.client.open(url[len(self.BASE):], method=method, headers=headers, data=body,
                             base_url=self.BASE)
        return r.status_code, r.get_data()

    def transport(self, project):
        return ad.HttpTransport(self.BASE, self._project(project)['pull'], send=self.send)

    def answer(self, project, task, bid, option='Orange'):
        from central import deliveries
        p = self._project(project)
        if (project, bid) not in self.raised:     # the runner's ledger reports the blocker to the centre first
            e = {'schema': 'event.v1', 'id': uuid.uuid4().hex, 'time': '2026-10-31T09:00:00+00:00', 'run': '20261031-0900',
                 'project': project, 'type': 'blocker', 'item': task, 'detail': {'action': 'raised', 'id': bid},
                 'evidence_class': 'agent_reported'}
            r = self.client.post('/api/v1/events', base_url=self.BASE, data=json.dumps(e),
                                 headers={'Authorization': 'Bearer ' + p['ingest'], 'Content-Type': 'application/json'})
            assert r.status_code == 200, r.get_json()
            self.raised.add((project, bid))
        owner = self.conn.query_one('SELECT * FROM account WHERE id = ?', (p['owner'],))
        return deliveries.record_decision(self.conn, p['row'], bid, owner, option=option)['delivery_id']

    def row_state(self, project, did):
        from central import deliveries
        row = deliveries.get(self.conn, self._project(project)['row'], did)
        return row and row['state']


WORLDS = {'local': LocalWorld, 'central': CentralWorld, 'http': HttpWorld}


@pytest.fixture(params=list(WORLDS))
def world(request, tmp_path):
    w = WORLDS[request.param](tmp_path)
    if hasattr(w, 'close'):
        request.addfinalizer(w.close)
    return w


def _raise(world, project, bid, task='A1'):
    b = ab.new_blocker(id=bid, project=project, task=task, run='r1', agent_role='implementer', stage=task,
                       question=f'Which colour for {project}?', why_needed='Content depends on it.', prevents='Writing.',
                       impact_if_delayed='Waits.', impact_if_wrong='Repaint.', owner_role='product owner',
                       options=[{'label': 'Orange', 'tradeoffs': 'Brand.'}, {'label': 'Grey', 'tradeoffs': 'Plain.'}])
    ab.Store(world.auto(project)).create(b)


def _state(world, project, bid):
    return ab.Store(world.auto(project)).load(bid)['state']


def _inbox_names(world, project):
    d = world.auto(project) / 'inbox'
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def test_two_projects_never_receive_each_others_answer(world):
    _raise(world, 'alpha', 'B-a1')
    _raise(world, 'beta', 'B-b1')                      # same item id in both projects
    da = world.answer('alpha', 'A1', 'B-a1', option='Orange')
    db = world.answer('beta', 'A1', 'B-b1', option='Grey')
    got = ad.pull(world.transport('alpha'), world.auto('alpha'), 'alpha')
    assert [e['delivery_id'] for e in got] == [da]
    assert _inbox_names(world, 'alpha') == [f'{da}.json'] and _inbox_names(world, 'beta') == []
    assert world.row_state('beta', db) != 'acknowledged' and _state(world, 'beta', 'B-b1') != 'DELIVERED'
    got = ad.pull(world.transport('beta'), world.auto('beta'), 'beta')
    assert [(e['delivery_id'], e['decision']['answer']['option']) for e in got] == [(db, 'Grey')]
    assert ad.resume(world.transport('alpha'), world.auto('alpha'), 'A1')[1] == ['B-a1']
    assert world.row_state('alpha', da) == 'acknowledged' and world.row_state('beta', db) != 'acknowledged'
    assert _state(world, 'beta', 'B-b1') == 'DELIVERED'
    packet, _ = ad.resume(world.transport('beta'), world.auto('beta'), 'A1')
    assert 'Chosen option: Grey' in packet and 'alpha' not in packet


def test_recovery_after_a_service_outage(world):
    _raise(world, 'alpha', 'B-a1')
    did = world.answer('alpha', 'A1', 'B-a1')
    before = _state(world, 'alpha', 'B-a1')
    t = Outage(world.transport('alpha'))
    t.down = {'pull'}
    with pytest.raises(ad.TransportUnavailable):
        ad.pull(t, world.auto('alpha'), 'alpha')
    assert _state(world, 'alpha', 'B-a1') == before and _inbox_names(world, 'alpha') == []    # nothing changed
    t.down = set()
    assert [e['delivery_id'] for e in ad.pull(t, world.auto('alpha'), 'alpha')] == [did]
    t.down = {'ack'}
    with pytest.raises(ad.TransportUnavailable):
        ad.resume(t, world.auto('alpha'), 'A1')
    assert _state(world, 'alpha', 'B-a1') == 'DELIVERED' and world.row_state('alpha', did) != 'acknowledged'
    assert ad.resumable(world.auto('alpha'), 'A1')                                             # still waiting, still resumable
    t.down = set()
    packet, ids = ad.resume(t, world.auto('alpha'), 'A1')
    assert ids == ['B-a1'] and 'Chosen option: Orange' in packet
    assert _state(world, 'alpha', 'B-a1') == 'RESUMED' and world.row_state('alpha', did) == 'acknowledged'


def test_recovery_after_a_runner_restart_and_a_crash_mid_resume(world, monkeypatch):
    _raise(world, 'alpha', 'B-a1')
    did = world.answer('alpha', 'A1', 'B-a1')
    ad.pull(world.transport('alpha'), world.auto('alpha'), 'alpha')
    # the runner process dies here; a new one starts with a fresh transport and store
    assert ad.pull(world.transport('alpha'), world.auto('alpha'), 'alpha') == []
    real = ab.Store.transition

    def crash(self, bid, to, key, **kw):
        if to == 'ACKNOWLEDGED':
            raise KeyboardInterrupt('runner killed (simulated)')
        return real(self, bid, to, key, **kw)
    monkeypatch.setattr(ab.Store, 'transition', crash)
    with pytest.raises(KeyboardInterrupt):
        ad.resume(world.transport('alpha'), world.auto('alpha'), 'A1')
    monkeypatch.setattr(ab.Store, 'transition', real)
    assert world.row_state('alpha', did) == 'acknowledged' and _state(world, 'alpha', 'B-a1') == 'DELIVERED'
    packet, ids = ad.resume(world.transport('alpha'), world.auto('alpha'), 'A1')        # the next start finishes it
    assert ids == ['B-a1'] and packet
    hist = [h['to'] for h in ab.Store(world.auto('alpha')).load('B-a1')['history']]
    assert hist.count('ACKNOWLEDGED') == 1 and hist.count('RESUMED') == 1
    assert ad.resume(world.transport('alpha'), world.auto('alpha'), 'A1') == ('', [])


def test_duplicate_pull_and_duplicate_ack_apply_the_decision_once(world):
    _raise(world, 'alpha', 'B-a1')
    did = world.answer('alpha', 'A1', 'B-a1')
    if isinstance(world, CentralWorld):
        world.central.duplicate = True                 # the service sends the same row twice in one reply
    t = world.transport('alpha')
    first = ad.pull(t, world.auto('alpha'), 'alpha')
    second = ad.pull(t, world.auto('alpha'), 'alpha')
    assert [e['delivery_id'] for e in first] == [did] and second == []
    assert _inbox_names(world, 'alpha') == [f'{did}.json']
    store = ab.Store(world.auto('alpha'))
    assert len(store.decisions('B-a1')) == 1
    t.ack(did)
    t.ack(did)                                         # a repeated ack changes nothing
    assert world.row_state('alpha', did) == 'acknowledged'
    _, ids = ad.resume(t, world.auto('alpha'), 'A1')
    assert ids == ['B-a1']
    hist = [h['to'] for h in store.load('B-a1')['history']]
    assert [hist.count(s) for s in ('ANSWERED', 'DELIVERED', 'ACKNOWLEDGED', 'RESUMED')] == [1, 1, 1, 1]
    assert ad.pull(t, world.auto('alpha'), 'alpha') == []                                # acknowledged: never offered again


def test_status_of_an_answered_but_undelivered_decision_is_accurate(world):
    _raise(world, 'alpha', 'B-a1')
    did = world.answer('alpha', 'A1', 'B-a1')
    state = _state(world, 'alpha', 'B-a1')
    # local: the answer is saved here and waits for delivery; central: the runner has not heard of it yet
    assert state == ('PENDING_DELIVERY' if isinstance(world, LocalWorld) else 'OPEN')
    assert ab.describe(state) in ('answer saved, waiting for delivery', 'waiting for an answer')
    assert not ab.is_unblocked(state) and not ad.task_answered(world.auto('alpha'), 'A1')
    assert not ad.resumable(world.auto('alpha'), 'A1')
    assert world.row_state('alpha', did) in (None, 'pending')                            # not delivered, not acknowledged
    ab.write_view(world.auto('alpha'))
    view = (world.auto('alpha') / 'QUESTIONS.md').read_text(encoding='utf-8')
    assert 'B-a1' in view and 'resumed' not in view.lower()
    ad.pull(world.transport('alpha'), world.auto('alpha'), 'alpha')
    assert ab.describe(_state(world, 'alpha', 'B-a1')) == 'answer delivered, waiting for the runner'


# The fake's own rules (only the central service has a hand-over that can be lost or refused).

def test_lost_reply_is_marked_failed_and_the_next_pull_delivers(tmp_path):
    w = CentralWorld(tmp_path)
    _raise(w, 'alpha', 'B-a1')
    did = w.answer('alpha', 'A1', 'B-a1')
    w.central.lose_next = 1
    assert ad.pull(w.transport('alpha'), w.auto('alpha'), 'alpha') == []
    row = w.central.rows[did]
    assert (row['state'], row['attempts']) == ('failed', 1) and 'lost' in row['last_error']
    assert _state(w, 'alpha', 'B-a1') == 'OPEN'
    assert [e['delivery_id'] for e in ad.pull(w.transport('alpha'), w.auto('alpha'), 'alpha')] == [did]
    assert (row['state'], row['attempts'], row['last_error']) == ('delivered', 2, None)
    with pytest.raises(KeyError):
        w.central.ack('B-nope.v1')


def test_central_refuses_an_ack_before_delivery_and_ignores_a_row_for_another_item(tmp_path):
    w = CentralWorld(tmp_path)
    _raise(w, 'alpha', 'B-a1')
    did = w.central.publish('alpha', 'A9', 'B-a1', option='Orange')   # names the wrong item for this blocker
    with pytest.raises(ValueError, match='only a delivered row'):
        w.central.ack(did)
    assert ad.pull(w.transport('alpha'), w.auto('alpha'), 'alpha') == []
    assert _state(w, 'alpha', 'B-a1') == 'OPEN' and _inbox_names(w, 'alpha') == []


def test_received_decision_is_never_overwritten(tmp_path):
    w = CentralWorld(tmp_path)
    _raise(w, 'alpha', 'B-a1')
    w.answer('alpha', 'A1', 'B-a1')
    ad.pull(w.transport('alpha'), w.auto('alpha'), 'alpha')
    store = ab.Store(w.auto('alpha'))
    other = dict(store.latest_decision('B-a1'), answer={'option': 'Grey'})
    with pytest.raises(ab.CorruptBlocker, match='never overwritten'):
        store.receive(other)
    assert store.latest_decision('B-a1')['answer'] == {'option': 'Orange'}


def test_runner_with_the_central_service_survives_outages(project):
    assert ar.run(project, max_iterations=4, claude=blocking_agent('A1', BLOCKER)) == 0
    auto = project / '.auto'
    bid = ab.Store(auto).all()[0][0]['id']
    central = FakeCentral()
    central.publish(project.name, 'A1', bid, option='Orange', text='The brand orange.')
    calls = []
    central.down = {'pull'}
    assert ar.run(project, max_iterations=2, claude=picking_agent(calls), transport=central.transport(), allow_dirty=True) == 0
    assert calls == [] and project_statuses(project)['A1'][0] == 'blocked' and ab.Store(auto).load(bid)['state'] == 'OPEN'
    central.down = {'ack'}
    assert ar.run(project, max_iterations=2, claude=picking_agent(calls), transport=central.transport(), allow_dirty=True) == 0
    st = project_statuses(project)['A1']
    assert calls == [] and st[0] == 'blocked' and st[1].startswith('(delivery unavailable)')
    assert ab.Store(auto).load(bid)['state'] == 'DELIVERED'
    central.down = set()
    assert ar.run(project, max_iterations=2, claude=picking_agent(calls), transport=central.transport(), allow_dirty=True) == 0
    assert calls == ['A1'] and project_statuses(project)['A1'][0] == 'done'
    assert ab.Store(auto).load(bid)['state'] == 'RESUMED' and central.rows[f'{bid}.v1']['state'] == 'acknowledged'
    acts = [e['detail']['action'] for e in events(project) if e['type'] == 'blocker']
    assert acts.count('delivered') == 1 and acts.count('resumed') == 1
