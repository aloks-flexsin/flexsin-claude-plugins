"""auto-runner plugin (R5): failed calls to the central service are recorded as run-level `central_error` frictions,
`http_<status>:<route>` (the request path with task numbers as `N`, no query string) or `unreachable`. No network: the
service is tests/auto_runner/fake_central.FakeTasks behind a `send` that also answers the event and delivery routes."""
import json
from urllib.parse import urlparse

import pytest

from tests.auto_runner.fake_central import FakeTasks
from tests.auto_runner.test_central_tasks import FAKE_ENV, connect
from tests.auto_runner.test_runner import ar, events, picking_agent, project  # noqa: F401  (project is a fixture)

at = ar.autotasks
ad = ar.autodeliver
BASE = 'https://central.test'
SLUG = 'acme-web'
HEARTBEAT = 'central_error:http_409:/api/v1/tasks/N/heartbeat'


class Service:
    """FakeTasks for the task API, plus POST /api/v1/events (refusing with `refuse_type`'s events with 400) and an empty
    GET /api/v1/deliveries/pending. `down` makes every call unreachable."""

    def __init__(self):
        self.tasks, self.down, self.refuse_type, self.stored = FakeTasks(), False, None, []

    def send(self, method, url, headers, body, timeout):
        if self.down:
            raise ConnectionRefusedError('no route (simulated)')
        path = urlparse(url).path
        if path == '/api/v1/deliveries/pending':
            return 200, b'{"deliveries": []}'
        if path == '/api/v1/events':
            batch = json.loads(body.decode('utf-8'))
            if any(e.get('type') == self.refuse_type for e in batch):
                return 400, b'{"error": "refused (simulated)"}'
            self.stored += batch
            return 200, json.dumps({'stored': len(batch)}).encode('utf-8')
        return self.tasks.send(method, url, headers, body, timeout)

    def transport(self):
        return ad.HttpTransport(BASE, FAKE_ENV['AUTO_RUNNER_TOKEN'], send=self.send, project=SLUG)


def central_errors(p):
    found = []
    for e in events(p):
        if e['type'] == 'friction' and e['detail']['kind'] == 'central_error':
            assert ar.autoevents.validate(e) == [] and e['item'] is None
            found.append(ar.autoevents.signature(e['detail']))
    return found


def current_task(auto, number, claim):
    path = auto / 'run' / 'current-task.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'number': number, 'claim': claim}), encoding='utf-8')


@pytest.fixture
def svc(project, monkeypatch):
    connect(project)
    for k, v in FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    return Service()


# ---------- autotasks: on_error(status, route) ----------

@pytest.mark.parametrize('path,route', [
    ('/api/v1/tasks/12/heartbeat?project=acme-web', '/api/v1/tasks/N/heartbeat'),
    ('/api/v1/tasks/7/report', '/api/v1/tasks/N/report'),
    ('/api/v1/events', '/api/v1/events'),
])
def test_the_route_has_no_task_number_and_no_query(path, route):
    assert at.error_route(path) == route


def test_beat_reports_a_lost_claim_and_still_says_lost(tmp_path):
    svc = Service()
    svc.tasks.add(12, 'CSV export', claim='claim-new')
    current_task(tmp_path, 12, 'claim-old')
    seen = []
    assert at.beat(svc.transport(), tmp_path, on_error=lambda s, r: seen.append((s, r))) == 'lost'
    assert seen == [(409, '/api/v1/tasks/N/heartbeat')]
    assert at.current(tmp_path) is None


def test_beat_reports_an_unreachable_or_failing_service_and_still_says_unavailable(tmp_path):
    svc = Service()
    svc.tasks.add(3, 'CSV export')
    current_task(tmp_path, 3, 'claim-1')
    seen = []
    svc.down = True
    assert at.beat(svc.transport(), tmp_path, on_error=lambda s, r: seen.append((s, r))) == 'unavailable'
    failing = ad.HttpTransport(BASE, 'arp_x', send=lambda *a: (503, b'{}'))
    assert at.beat(failing, tmp_path, 'building', on_error=lambda s, r: seen.append((s, r))) == 'unavailable'
    assert seen == [(None, '/api/v1/tasks/N/heartbeat'), (503, '/api/v1/tasks/N/heartbeat')]


def test_a_good_beat_calls_nothing_and_no_callback_is_needed(tmp_path):
    svc = Service()
    svc.tasks.add(3, 'CSV export')
    current_task(tmp_path, 3, 'claim-1')
    seen = []
    assert at.beat(svc.transport(), tmp_path, 'building', on_error=lambda s, r: seen.append((s, r))) == 'ok'
    assert seen == [] and svc.tasks.tasks[3]['status'] == 'building' and svc.tasks.heartbeats == [(3, 'building')]
    svc.down = True
    assert at.beat(svc.transport(), tmp_path) == 'unavailable'


def test_finish_and_wait_note_report_their_failures(tmp_path):
    svc = Service()
    svc.tasks.add(5, 'CSV export')
    req = tmp_path / 'req'
    req.mkdir()
    (req / 'task.json').write_text(json.dumps({'number': 5, 'claim': 'other'}), encoding='utf-8')
    seen = []
    assert at.wait_note(svc.transport(), req, 'waits', on_error=lambda s, r: seen.append((s, r))) is False
    current_task(tmp_path, 5, 'claim-1')
    line = at.finish(svc.transport(), tmp_path, True, 'b', None, 'done', on_error=lambda s, r: seen.append((s, r)))
    assert line.startswith('task 5: could not report')                 # the task is planning, not being built: 409
    assert seen == [(409, '/api/v1/tasks/N/heartbeat'), (409, '/api/v1/tasks/N/report')]


# ---------- autodeliver.push: skipped_status and error_status ----------

def test_push_names_the_status_of_a_skip_and_of_an_error(tmp_path):
    svc = Service()
    led = ar.autoevents.Ledger(tmp_path / '.auto', '20261125-0900', SLUG)
    led.emit('run_start')
    led.emit('run_end', detail={'exit': 0, 'reason': 'done'})
    svc.refuse_type = 'run_start'
    out = ad.push(svc.transport(), tmp_path / '.auto', SLUG)
    assert out['sent'] == 1 and len(out['skipped']) == 1 and out['error'] is None
    assert out['skipped_status'] == 400 and out['error_status'] is None
    led.emit('run_start')
    svc.down = True
    out = ad.push(svc.transport(), tmp_path / '.auto', SLUG)
    assert out['error'] and out['error_status'] is None and out['skipped_status'] is None
    svc.down = False
    out = ad.push(ad.HttpTransport(BASE, 'x', send=lambda *a: (401, b'{"error": "no"}')), tmp_path / '.auto', SLUG)
    assert out['error_status'] == 401
    out = ad.push(ad.HttpTransport(BASE, 'x', send=lambda *a: (502, b'')), tmp_path / '.auto', SLUG)
    assert out['error_status'] == 502


# ---------- a run records central_error frictions ----------

def test_a_stale_claim_gets_409_and_the_run_records_it(project, svc, capsys):
    svc.tasks.add(1, 'CSV export', claim='claim-1')
    current_task(project / '.auto', 1, 'claim-stale')
    # the run's first beat (a new branch) loses the claim: the run carries on without a central task, as today
    assert ar.run(project, max_iterations=3, claude=picking_agent(), transport=svc.transport(), allow_dirty=True) == 0
    assert 'task 1: claim lost' in capsys.readouterr().out
    assert central_errors(project) == [HEARTBEAT]
    assert any(e['type'] == 'friction' and e['detail']['subject'].startswith('http_409') for e in svc.stored)


def test_an_unreachable_service_is_recorded_once(project, svc):
    svc.down = True
    assert ar.run(project, max_iterations=2, claude=picking_agent(), transport=svc.transport(), allow_dirty=True) == 0
    assert central_errors(project) == ['central_error:unreachable']


def test_an_event_refused_with_400_is_recorded(project, svc):
    svc.refuse_type = 'run_start'
    assert ar.run(project, max_iterations=2, claude=picking_agent(), transport=svc.transport(), allow_dirty=True) == 0
    assert central_errors(project) == ['central_error:http_400:/api/v1/events']
    assert any(e['type'] == 'friction' and e['detail']['kind'] == 'central_error' for e in svc.stored)


def test_a_run_against_a_working_service_records_no_central_error(project, svc):
    assert ar.run(project, max_iterations=2, claude=picking_agent(), transport=svc.transport(), allow_dirty=True) == 0
    assert central_errors(project) == [] and svc.stored
