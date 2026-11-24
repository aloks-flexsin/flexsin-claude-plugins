"""
In-memory fake of the central delivery service (roadmap K6), built to auto-runner/docs/delivery-contract.md.

No network and no real service: a test answers a blocker "at the centre" with `publish()`, and the runner side reads it through
`FakeCentral.transport()`, a second `autodeliver.Transport` beside `LocalTransport`. Rows are kept per delivery id
(blocker id + decision version) with state pending | delivered | acknowledged | failed, an attempt count and the last error.
Faults are injected by the test: `down` (a set of call names, 'pull' and/or 'ack', that raise TransportUnavailable and change
nothing), `lose_next` (the next N
hand-overs are counted and marked failed, but the reply never reaches the runner) and `duplicate` (each row is sent twice in one reply).
"""
import copy
import json
import re
from urllib.parse import urlparse

from tests.auto_runner.test_runner import ar

ab = ar.autoblock
ad = ar.autodeliver


class FakeCentral:
    def __init__(self):
        self.rows = {}                            # delivery id -> row (project, task, blocker_id, version, decision, state, ...)
        self.down = set()                         # call names that fail: {'pull'}, {'ack'} or both
        self.lose_next = 0
        self.duplicate = False
        self.calls = []                           # ('pull', project) / ('ack', delivery_id), including refused ones

    def publish(self, project, task, blocker_id, responder='pat', role='product owner', option=None, text=None, context=''):
        """A person answers at the centre -> delivery id. The next version for the blocker; nothing earlier is changed."""
        version = 1 + max((r['version'] for r in self.rows.values() if r['blocker_id'] == blocker_id), default=0)
        decision = ab.new_decision(blocker_id, version, responder, role, option, text, context)
        did = ad.delivery_id(blocker_id, version)
        self.rows[did] = {'delivery_id': did, 'project': project, 'task': task, 'blocker_id': blocker_id, 'version': version,
                          'decision': decision, 'state': 'pending', 'attempts': 0, 'last_error': None}
        return did

    def state(self, did):
        return self.rows[did]['state']

    def _check(self, call):
        self.calls.append(call)
        if call[0] in self.down:
            raise ad.TransportUnavailable('central service unreachable (simulated)')

    def pull_pending(self, project):
        self._check(('pull', project))
        out = []
        for did in sorted(self.rows):
            r = self.rows[did]
            if r['project'] != project or r['state'] == 'acknowledged':
                continue
            r['attempts'] += 1
            if self.lose_next:
                self.lose_next -= 1
                r['state'], r['last_error'] = 'failed', 'reply lost before it reached the runner (simulated)'
                continue
            r['state'], r['last_error'] = 'delivered', None
            item = {k: copy.deepcopy(r[k]) for k in ('delivery_id', 'blocker_id', 'version', 'project', 'task', 'decision')}
            out += [item, copy.deepcopy(item)] if self.duplicate else [item]
        return out

    def ack(self, did):
        self._check(('ack', did))
        if did not in self.rows:
            raise KeyError(f'no delivery {did}')
        r = self.rows[did]
        if r['state'] not in ('delivered', 'acknowledged'):
            raise ValueError(f'delivery {did} is {r["state"]}: only a delivered row can be acknowledged')
        r['state'] = 'acknowledged'
        return dict(r)

    def transport(self):
        return CentralTransport(self)


class CentralTransport(ad.Transport):
    """What the runner holds: the two contract calls, nothing else of the fake."""

    def __init__(self, central):
        self.central = central

    def pull_pending(self, project):
        return self.central.pull_pending(project)

    def ack(self, did):
        return self.central.ack(did)


class FakeRunners:
    """In-memory fake of runner presence (`POST /api/v1/runners/beat`) for the watch tests, as a `send` for
    autodeliver.HttpTransport: beats are kept in `beats` (the JSON bodies) and answered with `control` (None, 'pause'
    or 'stop', which a test sets as a person would on the pages). `refuse` answers every beat with that status (e.g. 403);
    `down` makes beats unreachable. Every other call goes to `inner` (the real central's send, or FakeTasks.send)."""

    def __init__(self, inner):
        self.inner, self.beats, self.control, self.refuse, self.down = inner, [], None, None, False
        self.order = []                           # 'beat' or the other call's path, in the order they were sent

    def states(self):
        return [b['state'] for b in self.beats]

    def send(self, method, url, headers, body, timeout):
        path = urlparse(url).path
        if path != '/api/v1/runners/beat':
            self.order.append(path)
            return self.inner(method, url, headers, body, timeout)
        self.order.append('beat')
        if self.down:
            raise ConnectionRefusedError('no route (simulated)')
        if self.refuse:
            return self.refuse, json.dumps({'error': 'refused (simulated)'}).encode('utf-8')
        data = json.loads(body.decode('utf-8'))
        self.beats.append(data)
        return 200, json.dumps({'runner': {**data, 'control': self.control}, 'control': self.control}).encode('utf-8')


class FakeTasks:
    """In-memory fake of the central task API (personal token) for the planner and watch tests: a `send` for
    autodeliver.HttpTransport that answers GET /api/v1/tasks/N and POST /api/v1/tasks/N/plan (the claim must match;
    the task then waits in plan_review). `posted` keeps every plan the runner posted."""

    def __init__(self):
        self.tasks = {}                           # number -> task dict (number, title, description, comment, status, claim)
        self.posted = []

    def add(self, number, title, description='', comment='', claim='claim-1'):
        self.tasks[number] = {'number': number, 'title': title, 'description': description, 'comment': comment,
                              'status': 'planning', 'plan': '', 'items': '', 'claim': claim}
        return self.tasks[number]

    @staticmethod
    def _reply(status, data):
        return status, json.dumps(data).encode('utf-8')

    def send(self, method, url, headers, body, timeout):
        if urlparse(url).path == '/api/v1/tasks':
            return self._collection(method, body)
        m = re.fullmatch(r'/api/v1/tasks/(\d+)(/plan)?', urlparse(url).path)
        task = self.tasks.get(int(m.group(1))) if m else None
        if task is None:
            return self._reply(404, {'error': 'no such task'})
        public = {k: v for k, v in task.items() if k != 'claim'}
        if method == 'GET' and not m.group(2):
            return self._reply(200, {'task': public})
        if method == 'POST' and m.group(2):
            data = json.loads(body.decode('utf-8'))
            if data.get('claim') != task['claim']:
                return self._reply(409, {'error': 'the claim is not yours'})
            task.update(status='plan_review', plan=data['plan'], items=data['items'])
            self.posted.append({'number': task['number'], 'plan': data['plan'], 'items': data['items']})
            return self._reply(200, {'task': {k: v for k, v in task.items() if k != 'claim'}})
        return self._reply(405, {'error': 'not allowed'})

    def _collection(self, method, body):
        """GET /api/v1/tasks lists every task; POST /api/v1/tasks records one as the service does (building, or
        plan_review with `"approval": "page"`), claimed by the runner."""
        if method == 'GET':
            return self._reply(200, {'tasks': [
                {'number': t['number'], 'title': t['title'], 'status': t['status'], 'assignee': 'dev@example.com',
                 'runner': t.get('runner')} for t in sorted(self.tasks.values(), key=lambda t: t['number'])]})
        if method != 'POST':
            return self._reply(405, {'error': 'not allowed'})
        data = json.loads(body.decode('utf-8'))
        if data.get('approval') not in (None, 'terminal', 'page'):
            return self._reply(400, {'error': 'unknown approval'})
        number = max(self.tasks, default=0) + 1
        task = self.add(number, data['title'], data.get('description', ''), claim=f'claim-{number}')
        task.update(status='plan_review' if data.get('approval') == 'page' else 'building', plan=data.get('plan', ''),
                    items=data.get('items', ''), runner=data.get('runner'))
        return self._reply(200, {'task': {k: v for k, v in task.items() if k != 'claim'}, 'claim': task['claim']})
