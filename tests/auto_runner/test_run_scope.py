"""auto-runner plugin: a run on a central task works only that task's items (queue Y1). The queue is shared, so it can
hold another request's open items (a task still being built on another branch); the run leaves them alone, finishes
when its own items are ticked and reports its task done. A scratch git repo with a fake claude, and the watch step
against the in-memory task fake (no network)."""
import json

from tests.auto_runner.fake_central import FakeTasks
from tests.auto_runner.test_resume_request import ENV, ITEMS, aw, request, sender, worktree
from tests.auto_runner.test_runner import ar, picking_agent, project, write_queue  # noqa: F401  (project is a fixture)

X1 = ('- [ ] **X1 Another task.** Another request still being built on another branch, with enough words for any rule.\n'
      '  *Acceptance:* the file exists.\n  *Slice:* 1 Other work\n')
SHARED = '# Queue\n\n' + X1 + '\n## Needs a person\n- nothing\n'


def project_queue(p):
    return (p / '.auto' / 'queue.md').read_text(encoding='utf-8')


def current(p):
    return json.loads((p / '.auto' / 'run' / 'current-task.json').read_text(encoding='utf-8'))


# ---------- adopt and current keep the scope ----------

def test_adopt_keeps_the_items_when_given_and_omits_them_otherwise(tmp_path):
    auto = tmp_path / '.auto'
    req = tmp_path / 'req'
    req.mkdir()
    (req / 'task.json').write_text(json.dumps({'number': 7, 'claim': 'c1'}), encoding='utf-8')
    ar.autotasks.adopt(auto, req, ['R1', 'R2'])
    assert ar.autotasks.current(auto) == {'number': 7, 'claim': 'c1', 'items': ['R1', 'R2']}
    ar.autotasks.adopt(auto, req)
    assert 'items' not in ar.autotasks.current(auto)


def test_current_keeps_the_items_when_it_takes_a_newer_claim(tmp_path):
    auto = tmp_path / '.auto'
    req = tmp_path / 'req'
    req.mkdir()
    (req / 'task.json').write_text(json.dumps({'number': 7, 'claim': 'c1'}), encoding='utf-8')
    ar.autotasks.adopt(auto, req, ['R1', 'R2'])
    claims = auto / 'run' / 'tasks'
    claims.mkdir(parents=True, exist_ok=True)
    (claims / '7.json').write_text(json.dumps({'number': 7, 'claim': 'c2'}), encoding='utf-8')
    assert ar.autotasks.current(auto) == {'number': 7, 'claim': 'c2', 'items': ['R1', 'R2']}
    assert current(tmp_path)['items'] == ['R1', 'R2']


def test_run_scope_and_open_in():
    assert ar.open_in(SHARED + ITEMS) == 3 and ar.open_in(SHARED + ITEMS, {'R1', 'R2'}) == 2
    assert ar.first_open_id(SHARED + ITEMS) == 'X1' and ar.first_open_id(SHARED + ITEMS, None, {'R2'}) == 'R2'
    assert ar.first_open_id(SHARED, None, {'R1'}) is None


# ---------- the runner ----------

def test_a_run_on_a_task_works_only_its_own_items(project, capsys):
    write_queue(project, SHARED)
    request(project, task={'number': 7, 'claim': 'c1'})
    calls = []
    assert ar.run(project, max_iterations=4, claude=picking_agent(calls), enqueue='req', allow_dirty=True) == 0
    out = capsys.readouterr().out
    assert calls == ['R1', 'R2']
    assert "the task's items are done; 1 open item(s) of other requests left untouched" in out
    pq = project_queue(project)
    assert '- [ ] **X1' in pq and '- [x] **R1' in pq and '- [x] **R2' in pq
    assert 'X1' not in ar.queue.statuses(pq) or all(i != 'X1' for i, _, _ in ar.queue.statuses(pq))
    assert not (worktree(project) / 'X1.txt').exists()


def test_the_scope_survives_a_stop_and_a_resume(project, capsys):
    write_queue(project, SHARED)
    request(project, task={'number': 7, 'claim': 'c1'})
    calls = []
    assert ar.run(project, max_iterations=1, claude=picking_agent(calls), enqueue='req', allow_dirty=True) == 0
    assert calls == ['R1']
    request(project, task={'number': 7, 'claim': 'c2'})            # the task taken again: a newer claim
    capsys.readouterr()
    assert ar.run(project, max_iterations=4, claude=picking_agent(calls), enqueue='req', allow_dirty=True) == 0
    out = capsys.readouterr().out
    assert "resuming it" in out and calls == ['R1', 'R2']
    assert current(project) == {'number': 7, 'claim': 'c2', 'items': ['R1', 'R2']}
    assert '- [ ] **X1' in project_queue(project)


def test_a_current_task_without_items_works_every_item_as_before(project):
    write_queue(project, SHARED.replace('## Needs a person', ITEMS + '\n## Needs a person'))
    run = project / '.auto' / 'run'
    run.mkdir(parents=True, exist_ok=True)
    (run / 'current-task.json').write_text(json.dumps({'number': 7, 'claim': 'c1'}), encoding='utf-8')
    calls = []
    assert ar.run(project, max_iterations=4, claude=picking_agent(calls), allow_dirty=True) == 0
    assert calls == ['X1', 'R1', 'R2']


def test_without_a_current_task_the_order_is_unchanged(project):
    write_queue(project, SHARED.replace('## Needs a person', ITEMS + '\n## Needs a person'))
    calls = []
    assert ar.run(project, max_iterations=4, claude=picking_agent(calls), allow_dirty=True) == 0
    assert calls == ['X1', 'R1', 'R2']


# ---------- the watch step reports the task done ----------

def test_the_watcher_builds_its_task_beside_another_open_item_and_reports_it_done(project, monkeypatch, capsys):
    cfg_path = project / '.claude' / 'auto.json'
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    cfg['central'] = {'enabled': True, 'url': 'https://central.example', 'project': 'acme-web'}
    cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
    ar.git(project, 'commit', '-q', '-am', 'central')
    write_queue(project, SHARED)
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    svc = FakeTasks()
    svc.add(7, 'Two parts').update(status='approved', plan='## Intent\nTwo parts.\n', items=ITEMS)
    calls = []
    out = aw.step(project, picking_agent(calls), ENV, sender(svc))
    printed = capsys.readouterr().out
    assert calls == ['R1', 'R2'] and out['ok'] is True and out['exit'] == 0
    assert 'left untouched' in printed
    assert svc.reports[-1]['result'] == 'done' and svc.tasks[7]['status'] == 'done'
    assert '- [ ] **X1' in project_queue(project)
