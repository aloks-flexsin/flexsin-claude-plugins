"""auto-runner plugin: the watcher resumes a task request left waiting in `.auto/pending/` before it claims anything new
(queue E2): the order of pending requests, `autotasks.check_claim` against the in-memory task fake, and the watch step
with fake builds (no network, no CLI)."""
import importlib.util
import json
from pathlib import Path

import pytest

from tests.auto_runner.fake_central import FakeRunners, FakeTasks
from tests.auto_runner.test_runner import ar, project, write_queue  # noqa: F401  (project is a fixture)

BIN = Path(__file__).resolve().parents[2] / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autorunner_watch_waiting_t', BIN / 'autowatch.py')
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)

ENV = {'AUTO_RUNNER_TOKEN': 'arp_test-token', 'AUTO_RUNNER_USER': 'alok', 'AUTO_RUNNER_ACCOUNT': 'bhawish'}
POLL = 60


@pytest.fixture
def watched(project, monkeypatch):
    cfg_path = project / '.claude' / 'auto.json'
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    cfg['central'] = {'enabled': True, 'url': 'https://central.example', 'project': 'acme-web'}
    cfg['watch'] = {'poll_seconds': POLL}
    cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
    ar.git(project, 'commit', '-q', '-am', 'central')
    write_queue(project, '# Queue\n\n## Needs a person\n- nothing\n')
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    return project, FakeTasks()


def waiting(p, svc, n, claim='claim-a', held=None, served=None):
    """Task n queued on the fake (its claim `served`), with its request folder (task.json claim `claim`) and this
    computer's claim file (`held`) -> the folder."""
    svc.add(n, f'Task {n}', claim=served or claim)['status'] = 'queued'
    d = p / '.auto' / 'pending' / f'task-{n}-task-{n}'
    d.mkdir(parents=True)
    (d / 'items.md').write_text('- [ ] **T1 X.** y\n', encoding='utf-8')
    (d / 'plan.md').write_text(f'# Plan of task {n}\n\n## Intent\nX.\n', encoding='utf-8')
    (d / 'task.json').write_text(json.dumps({'number': n, 'claim': claim}), encoding='utf-8')
    claims = p / '.auto' / 'run' / 'tasks'
    claims.mkdir(parents=True, exist_ok=True)
    (claims / f'{n}.json').write_text(json.dumps({'number': n, 'claim': held or claim}), encoding='utf-8')
    return d


class Calls:
    """Records take_next and start; take_next answers no task, start must not be called."""

    def __init__(self, monkeypatch):
        self.taken, self.started = [], []
        monkeypatch.setattr(aw.autotasks, 'take_next', lambda *a: self.taken.append(a) or {'task': None})
        monkeypatch.setattr(aw.autotasks, 'start', lambda *a: self.started.append(a) or 'never')


def waiting_build(slugs):
    """A build that leaves its request pending (it waits for an earlier branch) and exits 0."""
    return lambda p, slug, *a: slugs.append(slug) or 0


# ---------- the order of pending requests ----------

def test_pending_requests_lists_task_folders_by_number_then_the_others(tmp_path):
    for name in ('zeta', 'task-10-x', 'alpha', 'task-9-x', 'task-100-y'):
        (tmp_path / '.auto' / 'pending' / name).mkdir(parents=True)
    (tmp_path / '.auto' / 'pending' / 'a-file').write_text('', encoding='utf-8')
    names = [d.name for d in ar.pending_requests(tmp_path)]
    assert names == ['task-9-x', 'task-10-x', 'task-100-y', 'alpha', 'zeta']
    assert ar.pending_requests(tmp_path / 'nothing') == []


# ---------- check_claim ----------

def test_check_claim_beats_queued_and_says_ok(watched):
    p, svc = watched
    svc.add(7, 'X', claim='c7')['status'] = 'queued'
    assert aw.autotasks.check_claim(p, 7, 'c7', ENV, svc.send) == 'ok'
    assert svc.heartbeats == [(7, 'queued')] and svc.tasks[7]['status'] == 'queued'


def test_check_claim_is_lost_on_409(watched):
    p, svc = watched
    svc.add(7, 'X', claim='newer')
    assert aw.autotasks.check_claim(p, 7, 'old', ENV, svc.send) == 'lost'


def test_check_claim_raises_task_error_on_403(watched):
    p, svc = watched
    svc.add(7, 'X', claim='c7')
    svc.refuse_heartbeat = 403
    with pytest.raises(aw.autotasks.TaskError, match='403'):
        aw.autotasks.check_claim(p, 7, 'c7', ENV, svc.send)


def test_check_claim_lets_an_unreachable_service_raise(watched):
    p, _ = watched

    def down(*a):
        raise ConnectionRefusedError('no route (simulated)')
    with pytest.raises(aw.autodeliver.TransportUnavailable):
        aw.autotasks.check_claim(p, 7, 'c7', ENV, down)


# ---------- the watch step ----------

def test_a_waiting_task_request_is_built_again_without_a_new_claim(watched, monkeypatch):
    p, svc = watched
    d = waiting(p, svc, 7)
    calls, slugs, started = Calls(monkeypatch), [], []
    out = aw.step(p, None, ENV, svc.send, build=waiting_build(slugs), on_start=lambda n, a: started.append((n, a)))
    assert slugs == [d.name] and started == [(7, 'build')]
    assert len(calls.taken) == 1 and calls.started == []     # it waited: E3 claims the next task in the same step
    assert out['task'] == 7 and out['action'] == 'build' and out['title'] == 'Plan of task 7'
    assert out['ok'] is True and out['waiting'] is True and out['detail'] == aw.WAITING_DETAIL
    assert svc.heartbeats == [(7, 'queued')] and svc.tasks[7]['status'] == 'queued'


def test_a_resumed_build_that_finishes_is_done(watched, monkeypatch):
    import shutil
    p, svc = watched
    waiting(p, svc, 7)
    Calls(monkeypatch)

    def build(project, slug, *a):
        shutil.rmtree(Path(project) / '.auto' / 'pending' / slug)
        return 0
    out = aw.step(p, None, ENV, svc.send, build=build)
    assert out['ok'] is True and 'waiting' not in out and out['detail'] == 'runner exit 0'


def test_a_resumed_build_that_raises_is_a_failure_without_reopen(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 7)
    Calls(monkeypatch)
    reopened = []
    monkeypatch.setattr(aw.autotasks, 'reopen_build', lambda *a: reopened.append(a))

    def build(*a):
        raise RuntimeError('the worktree could not be made')
    out = aw.step(p, None, ENV, svc.send, build=build)
    assert out['ok'] is False and out['detail'] == 'RuntimeError: the worktree could not be made'
    assert reopened == []


def test_with_tasks_9_and_10_waiting_the_step_builds_9(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 10)
    nine = waiting(p, svc, 9)
    Calls(monkeypatch)
    slugs = []
    assert aw.step(p, None, ENV, svc.send, build=waiting_build(slugs))['task'] == 9
    assert slugs == [nine.name]


def test_a_first_folder_with_another_claim_is_not_resumed(watched, monkeypatch):
    p, svc = watched
    d = waiting(p, svc, 7, claim='claim-a', held='claim-b')
    calls, slugs = Calls(monkeypatch), []
    assert aw.step(p, None, ENV, svc.send, build=waiting_build(slugs)) is None
    assert len(calls.taken) == 1 and slugs == [] and svc.heartbeats == [] and d.is_dir()


def test_a_non_task_request_first_is_not_resumed(watched, monkeypatch):
    p, svc = watched
    (p / '.auto' / 'pending' / 'req').mkdir(parents=True)
    calls, slugs = Calls(monkeypatch), []
    assert aw.step(p, None, ENV, svc.send, build=waiting_build(slugs)) is None
    assert len(calls.taken) == 1 and slugs == [] and svc.heartbeats == []


def test_only_the_first_entry_is_looked_at(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 7, claim='claim-a', held='claim-b')           # first in line, not ours any more
    waiting(p, svc, 8)                                            # ours, but second
    calls, slugs = Calls(monkeypatch), []
    assert aw.step(p, None, ENV, svc.send, build=waiting_build(slugs)) is None
    assert len(calls.taken) == 1 and slugs == []


def test_a_lost_claim_drops_the_claim_file_and_folder_then_claims(watched, monkeypatch):
    p, svc = watched
    d = waiting(p, svc, 7, served='claim-of-someone-else')
    calls, slugs = Calls(monkeypatch), []
    assert aw.step(p, None, ENV, svc.send, build=waiting_build(slugs)) is None
    assert not d.exists() and not (p / '.auto' / 'run' / 'tasks' / '7.json').exists()
    assert len(calls.taken) == 1 and slugs == []


def test_a_refused_claim_check_waits_one_poll_and_keeps_the_request(watched, monkeypatch, capsys):
    p, svc = watched
    d = waiting(p, svc, 7)
    svc.refuse_heartbeat = 403
    calls, slugs = Calls(monkeypatch), []
    clock = Clock()
    runners = FakeRunners(svc.send)
    assert aw.watch(p, None, ENV, runners.send, build=waiting_build(slugs), clock=clock.time, sleep=clock.sleep,
                    max_steps=1) == 0
    out = capsys.readouterr().out
    assert 'claim: failed, could not claim a task' in out and '403' in out
    beat = json.loads((p / '.auto' / 'run' / 'watch.json').read_text(encoding='utf-8'))
    assert beat['state'] == 'waiting_after_failure' and sum(clock.slept) == POLL
    assert d.is_dir() and (p / '.auto' / 'run' / 'tasks' / '7.json').exists()
    assert slugs == [] and calls.taken == []


def test_a_resumed_builds_claude_calls_go_through_the_meter(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 7)
    Calls(monkeypatch)
    clock = Clock()

    def claude(cmd, cwd):
        return 0, json.dumps({'type': 'result', 'result': 'ok', 'total_cost_usd': 0.4})

    def build(project, slug, claude_, env, send):
        claude_(['claude', '-p', 'Do ONLY item T1'], project)
        return 0
    meter = aw.Meter(p, clock.time)
    aw.step(p, claude, ENV, svc.send, build=build, meter=meter)
    spent = json.loads(aw.spend_path(p, clock.now).read_text(encoding='utf-8'))
    assert spent['spent_usd'] == 0.4
    assert [(c['task'], c['action']) for c in spent['calls']] == [(7, 'build')]


def test_the_watch_loop_resumes_a_waiting_request_and_reports_waiting(watched, monkeypatch, capsys):
    p, svc = watched
    waiting(p, svc, 7)
    calls, slugs = Calls(monkeypatch), []
    clock = Clock()
    runners = FakeRunners(svc.send)
    assert aw.watch(p, None, ENV, runners.send, build=waiting_build(slugs), clock=clock.time, sleep=clock.sleep,
                    max_steps=1) == 0
    assert 'task 7 (build): waiting, waits for an earlier branch to be merged' in capsys.readouterr().out
    assert 'building #7' in runners.states() and len(calls.taken) == 1 and len(slugs) == 1


class Clock:
    def __init__(self):
        self.now, self.slept = 1_800_000_000.0, []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds
