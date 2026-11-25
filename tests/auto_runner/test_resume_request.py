"""auto-runner plugin: a request taken again after its run stopped part-way resumes instead of being refused (queue B1).
The runner on a scratch git repo with a fake claude, and the watch step against the in-memory task fake (no network)."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

from tests.auto_runner.fake_central import FakeTasks
from tests.auto_runner.test_runner import ar, picking_agent, project, write_queue  # noqa: F401  (project is a fixture)

BIN = Path(__file__).resolve().parents[2] / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autorunner_watch_resume_t', BIN / 'autowatch.py')
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)

ENV = {'AUTO_RUNNER_TOKEN': 'arp_test-token', 'AUTO_RUNNER_USER': 'alok', 'AUTO_RUNNER_ACCOUNT': 'bhawish'}
EMPTY = '# Queue\n\n## Needs a person\n- nothing\n'
R1 = ('- [ ] **R1 First part.** Build the first part in a small file, with enough words to pass any length rule we have.\n'
      '  *Acceptance:* the file exists.\n  *Slice:* 1 The parts\n')
R2 = ('- [ ] **R2 Second part.** Build the second part in a small file, with enough words to pass any length rule we have.\n'
      '  *Acceptance:* the file exists.\n  *Slice:* 1 The parts\n')
ITEMS = R1 + '\n' + R2


def worktree(p):
    return p.parent / (p.name + '-auto')


def request(p, slug='req', items=ITEMS, task=None):
    d = p / '.auto' / 'pending' / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / 'items.md').write_text(items, encoding='utf-8')
    if task:
        (d / 'task.json').write_text(json.dumps(task), encoding='utf-8')
    return d


def branch(p):
    return ar.git(worktree(p), 'branch', '--show-current')


@pytest.fixture
def stopped(project):
    """A two-item request whose first run did R1 only (one iteration)."""
    write_queue(project, EMPTY)
    request(project)
    calls = []
    assert ar.run(project, max_iterations=1, claude=picking_agent(calls), enqueue='req', allow_dirty=True) == 0
    assert calls == ['R1']
    return project


# ---------- the fake's new routes ----------

def call(svc, method, path, payload=None):
    status, body = svc.send(method, f'https://central.example{path}', {}, json.dumps(payload or {}).encode('utf-8'), 5)
    return status, json.loads(body.decode('utf-8'))


def test_fake_hands_out_a_build_task_with_a_new_claim_each_time():
    svc = FakeTasks()
    svc.add(7, 'CSV export')['status'] = 'building'
    s1, first = call(svc, 'POST', '/api/v1/tasks/next')
    s2, second = call(svc, 'POST', '/api/v1/tasks/next')
    assert s1 == s2 == 200 and first['action'] == second['action'] == 'build'
    assert first['task']['number'] == 7 and first['claim'] != second['claim'] != 'claim-1'
    svc.tasks[7]['status'] = 'queued'
    assert call(svc, 'POST', '/api/v1/tasks/next')[1]['action'] == 'build'


def test_fake_heartbeat_with_a_stale_claim_gets_409():
    svc = FakeTasks()
    svc.add(7, 'CSV export')['status'] = 'building'
    old = call(svc, 'POST', '/api/v1/tasks/next')[1]['claim']
    new = call(svc, 'POST', '/api/v1/tasks/next')[1]['claim']
    assert call(svc, 'POST', '/api/v1/tasks/7/heartbeat', {'claim': old})[0] == 409
    assert call(svc, 'POST', '/api/v1/tasks/7/heartbeat', {'claim': new, 'state': 'building'})[0] == 200
    assert svc.beat_claims == [(7, new)]


def test_fake_records_a_report_and_refuses_a_stale_one():
    svc = FakeTasks()
    svc.add(7, 'CSV export')['status'] = 'building'
    old = call(svc, 'POST', '/api/v1/tasks/next')[1]['claim']
    new = call(svc, 'POST', '/api/v1/tasks/next')[1]['claim']
    assert call(svc, 'POST', '/api/v1/tasks/7/report', {'claim': old, 'result': 'done'})[0] == 409
    assert call(svc, 'POST', '/api/v1/tasks/7/report', {'claim': new, 'result': 'stopped', 'note': 'x'})[0] == 200
    assert svc.tasks[7]['status'] == 'building'
    assert call(svc, 'POST', '/api/v1/tasks/7/report', {'claim': new, 'result': 'done', 'branch': 'b'})[0] == 200
    assert [(r['claim'], r['result']) for r in svc.reports] == [(new, 'stopped'), (new, 'done')]
    assert svc.tasks[7]['status'] == 'done' and svc.tasks[7]['claim'] is None


# ---------- the runner ----------

def test_the_same_request_again_resumes_on_the_same_branch(stopped, capsys):
    first = branch(stopped)
    capsys.readouterr()
    pending = request(stopped)
    calls = []
    assert ar.run(stopped, max_iterations=3, claude=picking_agent(calls), enqueue='req', allow_dirty=True) == 0
    out = capsys.readouterr().out
    assert f"request 'req' is already queued on {first}: resuming it" in out
    assert not pending.exists() and calls == ['R2'] and branch(stopped) == first
    assert (worktree(stopped) / 'R1.txt').exists() and (worktree(stopped) / 'R2.txt').exists()
    pq = (stopped / '.auto' / 'queue.md').read_text(encoding='utf-8')
    assert '- [x] **R1' in pq and '- [x] **R2' in pq


def test_the_resumed_request_adopts_its_newest_claim(stopped):
    request(stopped, task={'number': 7, 'claim': 'claim-new'})
    current = stopped / '.auto' / 'run' / 'current-task.json'
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(json.dumps({'number': 7, 'claim': 'claim-old'}), encoding='utf-8')
    seen = []

    def claude(cmd, cwd):
        seen.append(ar.autotasks.current(stopped / '.auto'))
        return picking_agent()(cmd, cwd)
    assert ar.run(stopped, max_iterations=3, claude=claude, enqueue='req', allow_dirty=True) == 0
    assert seen and seen[0]['claim'] == 'claim-new'


def test_while_a_run_holds_the_lock_the_request_waits_untouched(stopped, capsys):
    pending = request(stopped, task={'number': 7, 'claim': 'claim-new'})
    current = stopped / '.auto' / 'run' / 'current-task.json'
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(json.dumps({'number': 7, 'claim': 'claim-old'}), encoding='utf-8')
    before = {f.name: f.read_text(encoding='utf-8') for f in pending.iterdir()}
    (stopped / '.auto' / 'lock').write_text(str(os.getpid()), encoding='utf-8')       # a live run
    capsys.readouterr()
    calls = []
    assert ar.run(stopped, max_iterations=3, claude=picking_agent(calls), enqueue='req', allow_dirty=True) == 0
    out = capsys.readouterr().out
    assert "a run is in progress. Request 'req' is checked and waiting" in out and 'resuming' not in out
    assert {f.name: f.read_text(encoding='utf-8') for f in pending.iterdir()} == before
    assert json.loads(current.read_text(encoding='utf-8'))['claim'] == 'claim-old' and not calls


def test_a_request_with_one_queued_id_and_one_new_is_refused(stopped, capsys):
    R3 = R2.replace('R2 Second', 'R3 Third')
    pending = request(stopped, items=R1 + '\n' + R3)
    assert ar.run(stopped, max_iterations=3, claude=picking_agent(), enqueue='req', allow_dirty=True) == 1
    out = capsys.readouterr().out
    assert 'Not added' in out and 'resuming' not in out and pending.exists()


def test_a_request_whose_item_text_changed_is_refused(stopped, capsys):
    pending = request(stopped, items=R1 + '\n' + R2.replace('Second part.', 'Second part, changed.'))
    assert ar.run(stopped, max_iterations=3, claude=picking_agent(), enqueue='req', allow_dirty=True) == 1
    out = capsys.readouterr().out
    assert 'Not added' in out and 'resuming' not in out and pending.exists()


def test_item_text_ignores_the_tick_and_the_status_line():
    queued = R1.replace('- [ ]', '- [x]') + '  *Status:* done 1234abc\n'
    assert ar.already_queued(EMPTY.replace('## Needs', queued + '\n' + R2 + '\n## Needs'), ITEMS)
    assert not ar.already_queued(EMPTY, ITEMS) and not ar.already_queued(R1, '')


# ---------- the watch step ----------

@pytest.fixture
def watched(project, monkeypatch):
    cfg_path = project / '.claude' / 'auto.json'
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    cfg['central'] = {'enabled': True, 'url': 'https://central.example', 'project': 'acme-web'}
    cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
    ar.git(project, 'commit', '-q', '-am', 'central')
    write_queue(project, EMPTY)
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    svc = FakeTasks()
    task = svc.add(7, 'Two parts')
    task.update(status='approved', plan='## Intent\nTwo parts.\n', items=ITEMS)
    return project, svc


def sender(svc):
    """The task fake, with the delivery pull answered (nothing waiting); event pushes stay unanswered (not fatal)."""
    def send(method, url, headers, body, timeout):
        if '/api/v1/deliveries/pending' in url:
            return 200, json.dumps({'deliveries': []}).encode('utf-8')
        return svc.send(method, url, headers, body, timeout)
    return send


def test_a_task_stopped_after_its_first_item_resumes_when_taken_again(watched, capsys):
    p, svc = watched
    calls = []
    inner = picking_agent(calls)

    def stop_after_first(cmd, cwd):
        out = inner(cmd, cwd)
        (p / '.auto' / 'STOP').write_text('', encoding='utf-8')
        return out
    first = aw.step(p, stop_after_first, ENV, sender(svc))
    assert first['action'] == 'build' and calls == ['R1']
    assert [r['result'] for r in svc.reports] == ['stopped'] and svc.tasks[7]['status'] == 'building'
    old_branch = branch(p)
    (p / '.auto' / 'STOP').unlink()
    capsys.readouterr()

    out = aw.step(p, picking_agent(calls), ENV, sender(svc))
    printed = capsys.readouterr().out
    assert "is already queued on " + old_branch + ": resuming it" in printed
    assert not (p / '.auto' / 'pending').exists() or not list((p / '.auto' / 'pending').iterdir())
    assert calls == ['R1', 'R2'] and branch(p) == old_branch
    assert out['ok'] is True and out['exit'] == 0
    last = svc.reports[-1]
    assert last['result'] == 'done' and last['claim'] == 'claim-7-2'          # the claim taken the second time
    assert svc.reports[0]['claim'] == 'claim-7-1' and svc.tasks[7]['status'] == 'done'
