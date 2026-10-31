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
