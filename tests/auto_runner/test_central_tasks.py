"""auto-runner plugin: central tasks (decision record 0007, step 4) against the real central app (Flask test client, no
socket): take the next task, post its plan, build it after approval, record a terminal task, and a runner that beats the
claim, stops when an admin releases it, and reports the task done."""
import json

import pytest

from tests.auto_runner.test_init_central import AT, BASE, SLUG, central  # noqa: F401  (central is a fixture)
from tests.auto_runner.test_runner import ar, picking_agent, project, sh  # noqa: F401  (project is a fixture)

at = ar.autotasks
ITEMS = ('- [ ] **T1 Export.** Build the export in a small file, with enough words to pass any length rule we might have.\n'
         '  *Acceptance:* the file exists.\n  *Slice:* 1 The export\n')


@pytest.fixture
def team(central):
    """acme-web with developer dev@example.com and an admin; a personal token for dev."""
    from central import accounts, personal, projects
    dev = accounts.create(central.conn, 'dev@example.com', 'violet-anchor-58-tide')
    projects.add_member(central.conn, central.project['id'], 'dev@example.com', 'developer', AT)
    admin = accounts.create(central.conn, 'admin@example.com', 'violet-anchor-58-tide', role='admin')
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    return {'dev': dev, 'admin': admin, 'token': personal.create_token(central.conn, dev['id'], 'laptop', now), 'now': now}


def connect(p):
    cfg_path = p / '.claude' / 'auto.json'
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    cfg['central'] = {'enabled': True, 'url': BASE, 'project': SLUG}
    cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
    sh(p, 'add', '.claude/auto.json')
    sh(p, 'commit', '-q', '-m', 'central')


def add_task(central, team, title='CSV export'):
    from central import tasks
    return tasks.create(central.conn, central.project, team['dev'], team['dev'], title, 'Export as CSV.', team['now'])


def server_task(central, number):
    from central import tasks
    return tasks.get(central.conn, central.project['id'], number)


@pytest.fixture
def env(team):
    return {'AUTO_RUNNER_TOKEN': team['token'], 'AUTO_RUNNER_USER': 'alok', 'AUTO_RUNNER_ACCOUNT': 'bhawish'}


def test_next_plan_approve_start(project, central, team, env):
    connect(project)
    assert at.take_next(project, env, central.send) == {'task': None}
    n = add_task(central, team)['number']
    reply = at.take_next(project, env, central.send)
    assert reply['action'] == 'plan' and reply['task']['description'] == 'Export as CSV.'
    assert json.loads((project / '.auto' / 'run' / 'tasks' / f'{n}.json').read_text())['claim'] == reply['claim']
    assert server_task(central, n)['claim_runner'] == 'alok/bhawish-claude-auto-runner'
    assert at.post_plan(project, n, '## Intent\nCSV.', ITEMS, env, central.send)['status'] == 'plan_review'
    from central import tasks
    tasks.decide(central.conn, central.project, server_task(central, n), team['dev'], 'approve', '', team['now'])
    assert at.take_next(project, env, central.send)['action'] == 'build'
    slug = at.start(project, n, env, central.send)
    folder = project / '.auto' / 'pending' / slug
    assert slug == f'task-{n}-csv-export' and (folder / 'items.md').read_text() == ITEMS
    assert json.loads((folder / 'task.json').read_text())['number'] == n


def test_a_project_token_cannot_take_tasks(project, central, team):
    connect(project)
    with pytest.raises(at.TaskError, match='signed in'):
        at.take_next(project, {'AUTO_RUNNER_CENTRAL_TOKEN': central.token}, central.send)


def test_record_a_terminal_task(project, central, team, env):
    connect(project)
    n = at.record(project, 'csv-export', 'CSV export', 'plan', ITEMS, env, central.send)
    assert server_task(central, n)['status'] == 'queued'     # waits for its run to start (Q1)
    assert json.loads((project / '.auto' / 'pending' / 'csv-export' / 'task.json').read_text())['number'] == n
    assert [t['title'] for t in at.listing(project, env, central.send)] == ['CSV export']


def _runner_with_task(project, central, team, env, monkeypatch):
    connect(project)
    n = at.record(project, 'csv-export', 'CSV export', 'plan', ITEMS, env, central.send)
    (project / '.auto' / 'pending' / 'csv-export' / 'items.md').write_text(ITEMS, encoding='utf-8')
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    transport = ar.autodeliver.HttpTransport(BASE, team['token'], send=central.send, project=SLUG)
    return n, transport


def test_the_runner_beats_the_claim_and_reports_done(project, central, team, env, monkeypatch, capsys):
    n, transport = _runner_with_task(project, central, team, env, monkeypatch)
    assert ar.run(project, max_iterations=5, claude=picking_agent(), transport=transport, allow_dirty=True) == 0
    row = server_task(central, n)
    assert row['status'] == 'done' and row['claim_id'] is None and row['branch'].startswith('alok/bhawish-claude-auto-runner/')
    beats = [u for m, u, _ in central.calls if u.endswith(f'/api/v1/tasks/{n}/heartbeat?project={SLUG}')]
    assert len(beats) >= 3                                                     # one before every iteration
    assert f'task {n} reported done' in capsys.readouterr().out
    assert at.current(project / '.auto') is None


def test_a_released_task_stops_the_run(project, central, team, env, monkeypatch, capsys):
    n, transport = _runner_with_task(project, central, team, env, monkeypatch)
    from central import tasks
    inner, released = picking_agent(), []

    def claude(cmd, cwd):                                   # released while the first item is being worked
        if not released:
            released.append(True)
            tasks.release(central.conn, central.project, server_task(central, n), None, team['now'])
        return inner(cmd, cwd)

    assert ar.run(project, max_iterations=5, claude=claude, transport=transport, allow_dirty=True) == 1
    assert 'was released, reassigned or its claim expired' in capsys.readouterr().out
    events = [json.loads(line) for line in (project / '.auto' / 'events.jsonl').read_text().splitlines()]
    assert events[-1]['type'] == 'run_end' and events[-1]['detail']['reason'] == 'claim_lost'


def test_run_start_and_end_name_the_task(project, central, team, env, monkeypatch):
    n, transport = _runner_with_task(project, central, team, env, monkeypatch)
    assert ar.run(project, max_iterations=5, claude=picking_agent(), transport=transport, allow_dirty=True) == 0
    events = [json.loads(line) for line in (project / '.auto' / 'events.jsonl').read_text().splitlines()]
    start, end = events[0], events[-1]
    assert start['type'] == 'run_start' and start['detail']['task'] == n
    assert end['type'] == 'run_end' and end['detail']['task'] == n


# ---------- propose: a terminal plan approved on the page (against fake_central.FakeTasks) ----------

FAKE_ENV = {'AUTO_RUNNER_TOKEN': 'arp_test-token', 'AUTO_RUNNER_USER': 'alok', 'AUTO_RUNNER_ACCOUNT': 'bhawish'}


def test_propose_sends_the_plan_for_approval_on_the_page(project):
    """`propose` records the task as plan_review, keeps the claim on this computer, and `list` shows it as waiting
    for approval."""
    from tests.auto_runner.fake_central import FakeTasks
    connect(project)
    svc = FakeTasks()
    n = at.propose(project, 'CSV export', '## Intent\nCSV.', ITEMS, FAKE_ENV, svc.send)
    task = svc.tasks[n]
    assert task['status'] == 'plan_review' and task['items'] == ITEMS
    assert task['runner'] == 'alok/bhawish-claude-auto-runner'
    assert json.loads((project / '.auto' / 'run' / 'tasks' / f'{n}.json').read_text())['claim'] == task['claim']
    lines = [at.line(t) for t in at.listing(project, FAKE_ENV, svc.send)]
    assert lines == [f'#{n} [waiting for approval] CSV export - dev@example.com (worked by alok/bhawish-claude-auto-runner)']
    with pytest.raises(at.TaskError, match='--items'):
        at.propose(project, 'Empty', 'plan', '  ', FAKE_ENV, svc.send)
    assert len(svc.tasks) == 1
    n2 = at.record(project, 'csv-import', 'CSV import', 'plan', ITEMS, FAKE_ENV, svc.send)
    assert svc.tasks[n2]['status'] == 'building'                       # record (the default) is unchanged


def test_propose_approve_then_build_against_the_real_service(project, central, team, env):
    connect(project)
    n = at.propose(project, 'CSV export', '## Intent\nCSV.', ITEMS, env, central.send)
    assert server_task(central, n)['status'] == 'plan_review'
    assert at.take_next(project, env, central.send) == {'task': None}         # waits for a person
    from central import tasks
    tasks.decide(central.conn, central.project, server_task(central, n), team['dev'], 'approve', '', team['now'])
    assert at.take_next(project, env, central.send)['action'] == 'build'
    slug = at.start(project, n, env, central.send)
    assert (project / '.auto' / 'pending' / slug / 'items.md').read_text() == ITEMS


def test_propose_and_list_from_the_command_line(project, monkeypatch, capsys):
    from tests.auto_runner.fake_central import FakeTasks
    connect(project)
    svc = FakeTasks()
    real = ar.autodeliver.HttpTransport
    monkeypatch.setattr(at.autodeliver, 'HttpTransport', lambda *a, **k: real(*a, **dict(k, send=svc.send)))
    for k, v in FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    (project / 'plan.md').write_text('## Intent\nCSV.', encoding='utf-8')
    (project / 'items.md').write_text(ITEMS, encoding='utf-8')
    assert at.main(['propose', '--project', str(project), '--title', 'CSV export',
                    '--plan', str(project / 'plan.md'), '--items', str(project / 'items.md')]) == 0
    assert 'waits for approval' in capsys.readouterr().out and svc.tasks[1]['status'] == 'plan_review'
    assert at.main(['list', '--project', str(project)]) == 0
    assert '#1 [waiting for approval] CSV export' in capsys.readouterr().out


# ---------- the runner gets the screenshots (S4) ----------

PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(32))


def _fake_cli(project, monkeypatch):
    from tests.auto_runner.fake_central import FakeTasks
    connect(project)
    svc = FakeTasks()
    real = at.autodeliver.HttpTransport          # autotasks' own copy, so its TransportRefused is the one it catches
    monkeypatch.setattr(at.autodeliver, 'HttpTransport', lambda *a, **k: real(*a, **dict(k, send=svc.send)))
    for k, v in FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    return svc


def test_next_downloads_the_screenshots_once_and_prints_their_paths(project, monkeypatch, capsys):
    svc = _fake_cli(project, monkeypatch)
    svc.add(1, 'Fix the header', 'It overlaps the logo.')
    svc.attach(1, 'a1', 'header bug.png', PNG)
    svc.attach(1, 'b2', 'header bug.png', PNG + b'second')                 # same name: no collision
    svc.attach(1, 'c3', '../evil.png', PNG + b'evil')
    assert at.main(['next', '--project', str(project)]) == 0
    out = capsys.readouterr().out
    folder = (project / '.auto' / 'run' / 'tasks' / '1' / 'attachments').resolve()
    names = sorted(p.name for p in folder.iterdir())
    assert names == ['a1-header_bug.png', 'b2-header_bug.png', 'c3-evil.png']
    assert (folder / 'a1-header_bug.png').read_bytes() == PNG
    assert (folder / 'b2-header_bug.png').read_bytes() == PNG + b'second'
    assert (folder / 'c3-evil.png').read_bytes() == PNG + b'evil'         # written inside the folder
    assert not (project / '.auto' / 'run' / 'tasks' / '1' / 'evil.png').exists()
    assert '--- Screenshots ---' in out
    for name in names:
        assert str(folder / name) in out
    assert len(svc.downloads) == 3
    assert at.main(['next', '--project', str(project)]) == 0              # again: nothing is downloaded twice
    assert len(svc.downloads) == 3 and str(folder / 'a1-header_bug.png') in capsys.readouterr().out


def test_next_without_screenshots_prints_no_section_and_a_failed_download_does_not_stop_it(project, monkeypatch, capsys):
    svc = _fake_cli(project, monkeypatch)
    svc.add(1, 'Fix the header')
    assert at.main(['next', '--project', str(project)]) == 0
    assert 'Screenshots' not in capsys.readouterr().out
    svc.attach(1, 'gone', 'x.png', PNG)
    del svc.files[(1, 'gone')]                                             # deleted on the page meanwhile
    assert at.main(['next', '--project', str(project)]) == 0
    out = capsys.readouterr().out
    assert 'Not downloaded: x.png: could not download' in out and 'Task 1: Fix the header' in out
    assert not (project / '.auto' / 'run' / 'tasks' / '1' / 'attachments' / 'gone-x.png').exists()


@pytest.mark.parametrize('attachment_id, filename, expected', [
    ('a1', '../evil.png', 'a1-evil.png'),
    ('a1', '..\\..\\evil.png', 'a1-evil.png'),
    ('a1', '..', 'a1-..'),
    ('a1', 'my shot (2).png', 'a1-my_shot__2_.png'),
    ('a1', '', 'a1-screenshot'),
    ('../x', 'y.png', '.._x-y.png'),
    ('..', 'y.png', None),
    ('', 'y.png', None),
])
def test_attachment_names_stay_in_the_folder(attachment_id, filename, expected):
    assert at.attachment_name(attachment_id, filename) == expected


def test_next_downloads_from_the_real_service(project, central, team, env):
    from central import attachments
    connect(project)
    n = add_task(central, team)['number']
    row = attachments.add(central.conn, central.project, n, team['dev'], 'shot.png', PNG, team['now'])
    at.take_next(project, env, central.send)
    paths, problems = at.fetch_attachments(project, n, env, central.send)
    assert problems == [] and [p.name for p in paths] == [f'{row["id"]}-shot.png'] and paths[0].read_bytes() == PNG


# ---------- a resumed run keeps its task's newest claim (F1) ----------

def test_a_resumed_run_beats_with_the_newest_claim(project, central, team, env, monkeypatch):
    """The run adopted task.json (old claim); `next` then re-claimed the task, saving a new claim in run/tasks/<N>.json.
    `current` hands back the new claim and rewrites current-task.json, so `beat` gets ok instead of losing the task."""
    n, transport = _runner_with_task(project, central, team, env, monkeypatch)
    auto = project / '.auto'
    at.adopt(auto, auto / 'pending' / 'csv-export')
    old = at.current(auto)['claim']
    assert at.beat(transport, auto) == 'ok'                                 # the run started: queued -> building
    reply = at.take_next(project, env, central.send)
    assert reply['task']['number'] == n and reply['claim'] != old         # the server issued a new claim
    assert at.current(auto)['claim'] == reply['claim']
    assert json.loads((auto / 'run' / 'current-task.json').read_text())['claim'] == reply['claim']
    assert at.beat(transport, auto) == 'ok'
    assert server_task(central, n)['claim_id'] == reply['claim']


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def test_matching_claims_leave_current_task_unchanged(tmp_path):
    auto = tmp_path / '.auto'
    current = auto / 'run' / 'current-task.json'
    _write(current, {'number': 4, 'claim': 'c-1'})
    _write(auto / 'run' / 'tasks' / '4.json', {'number': 4, 'claim': 'c-1', 'title': 'T'})
    before = current.read_text(encoding='utf-8')
    assert at.current(auto) == {'number': 4, 'claim': 'c-1'}
    assert current.read_text(encoding='utf-8') == before


def test_no_current_task_stays_missing(tmp_path):
    auto = tmp_path / '.auto'
    _write(auto / 'run' / 'tasks' / '4.json', {'number': 4, 'claim': 'c-2'})
    assert at.current(auto) is None
    assert not (auto / 'run' / 'current-task.json').exists()


# ---------- queued while it waits, building when the run starts (Q2) ----------

def _spy(central):
    """A send that keeps the heartbeat bodies of the real central app."""
    beats = []

    def send(method, url, headers, body, timeout):
        if url.split('?')[0].endswith('/heartbeat'):
            beats.append(json.loads(body.decode('utf-8')))
        return central.send(method, url, headers, body, timeout)
    return beats, send


def _approved_task(project, central, team, env):
    from central import tasks
    connect(project)
    n = add_task(central, team)['number']
    at.take_next(project, env, central.send)
    at.post_plan(project, n, '## Intent\nCSV.', ITEMS, env, central.send)
    tasks.decide(central.conn, central.project, server_task(central, n), team['dev'], 'approve', '', team['now'])
    at.take_next(project, env, central.send)
    return n


def test_start_marks_the_task_queued(project, central, team, env):
    n = _approved_task(project, central, team, env)
    assert server_task(central, n)['status'] == 'building'
    beats, send = _spy(central)
    at.start(project, n, env, send)
    assert beats == [{'claim': server_task(central, n)['claim_id'], 'state': 'queued'}]
    assert server_task(central, n)['status'] == 'queued'


def test_a_run_taking_the_request_makes_the_task_building(project, central, team, env, monkeypatch):
    n = _approved_task(project, central, team, env)
    slug = at.start(project, n, env, central.send)
    assert server_task(central, n)['status'] == 'queued'
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    beats, send = _spy(central)
    transport = ar.autodeliver.HttpTransport(BASE, team['token'], send=send, project=SLUG)
    assert ar.run(project, max_iterations=5, claude=picking_agent(), transport=transport, enqueue=slug,
                  allow_dirty=True) == 0
    assert beats[0]['state'] == 'building'
    assert server_task(central, n)['status'] == 'done'


def test_a_lost_claim_on_the_first_heartbeat_does_not_stop_the_run(project, central, team, env, monkeypatch, capsys):
    from central import tasks
    n = _approved_task(project, central, team, env)
    slug = at.start(project, n, env, central.send)
    tasks.release(central.conn, central.project, server_task(central, n), None, team['now'])
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    transport = ar.autodeliver.HttpTransport(BASE, team['token'], send=central.send, project=SLUG)
    assert ar.run(project, max_iterations=5, claude=picking_agent(), transport=transport, enqueue=slug,
                  allow_dirty=True) == 0
    assert 'claim lost' in capsys.readouterr().out
    assert server_task(central, n)['status'] == 'approved'


# ---------- why a queued task waits (Q3) ----------

def _two_requests(project, central, team, env):
    """Task A's request (first) and task B's (it will wait): both started, so both queued -> (slug A, B's number)."""
    from central import tasks
    a = _approved_task(project, central, team, env)
    slug_a = at.start(project, a, env, central.send)
    b = add_task(central, team, 'Second thing')['number']
    at.take_next(project, env, central.send)
    at.post_plan(project, b, '## Intent\nTwo.', ITEMS.replace('T1', 'T2'), env, central.send)
    tasks.decide(central.conn, central.project, server_task(central, b), team['dev'], 'approve', '', team['now'])
    at.take_next(project, env, central.send)
    at.start(project, b, env, central.send)
    return slug_a, b


def test_a_waiting_request_sends_its_reason_and_keeps_its_claim(project, central, team, env, monkeypatch):
    slug_a, b = _two_requests(project, central, team, env)
    claim = server_task(central, b)['claim_id']
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    beats, send = _spy(central)
    transport = ar.autodeliver.HttpTransport(BASE, team['token'], send=send, project=SLUG)
    assert ar.run(project, max_iterations=5, claude=picking_agent(), transport=transport, enqueue=slug_a,
                  allow_dirty=True) == 0
    noted = [x for x in beats if x.get('note')]
    assert noted and noted[0]['state'] == 'queued' and noted[0]['claim'] == claim
    assert noted[0]['note'].startswith('waits: ') and 'is not merged into' in noted[0]['note']
    row = server_task(central, b)
    assert row['status'] == 'queued' and row['claim_id'] == claim and 'waits: ' in row['note']
    at.reopen_build(project, b, env, central.send)                    # it starts building: the note goes
    assert not server_task(central, b)['note']


def test_a_service_error_while_noting_a_wait_does_not_stop_the_run(project, central, team, env, monkeypatch, capsys):
    slug_a, b = _two_requests(project, central, team, env)

    def send(method, url, headers, body, timeout):
        if url.split('?')[0].endswith('/heartbeat') and b'"note"' in body:
            return 503, b'{"error": "down"}'
        return central.send(method, url, headers, body, timeout)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    transport = ar.autodeliver.HttpTransport(BASE, team['token'], send=send, project=SLUG)
    assert ar.run(project, max_iterations=5, claude=picking_agent(), transport=transport, enqueue=slug_a,
                  allow_dirty=True) == 0
    assert 'waits:' in capsys.readouterr().out
    assert server_task(central, b)['status'] == 'queued'


def test_wait_note_without_a_task_json_does_nothing(tmp_path):
    (tmp_path / 'req').mkdir()
    assert at.wait_note(object(), tmp_path / 'req', 'waits: x') is False
# ---------- decide a waiting plan from the terminal (P2) ----------

def _waiting(svc, number=1):
    task = svc.add(number, 'CSV export')
    task['status'] = 'plan_review'
    return task


def test_decide_approve_from_the_command_line(project, monkeypatch, capsys):
    svc = _fake_cli(project, monkeypatch)
    _waiting(svc)
    assert at.main(['decide', '1', 'approve', '--project', str(project)]) == 0
    assert capsys.readouterr().out.strip() == 'Task 1: approved'
    assert svc.tasks[1]['status'] == 'approved' and svc.decisions == [(1, 'approve', '')]


def test_decide_changes_with_a_comment_and_reject(project, monkeypatch, capsys):
    svc = _fake_cli(project, monkeypatch)
    _waiting(svc, 1)
    _waiting(svc, 2)
    assert at.main(['decide', '1', 'changes', '--comment', 'Add a header row', '--project', str(project)]) == 0
    assert capsys.readouterr().out.strip() == 'Task 1: changes requested'
    assert svc.tasks[1]['status'] == 'changes_requested' and svc.tasks[1]['comment'] == 'Add a header row'
    assert at.main(['decide', '2', 'reject', '--project', str(project)]) == 0
    assert capsys.readouterr().out.strip() == 'Task 2: rejected'
    assert svc.tasks[2]['status'] == 'rejected' and svc.tasks[2]['claim'] is None


def test_decide_changes_without_a_comment_prints_the_refusal(project, monkeypatch, capsys):
    svc = _fake_cli(project, monkeypatch)
    _waiting(svc)
    assert at.main(['decide', '1', 'changes', '--project', str(project)]) == 1
    out = capsys.readouterr().out
    assert out.startswith('Not done: ') and 'say what should change' in out
    assert svc.tasks[1]['status'] == 'plan_review' and svc.decisions == []


def test_decide_a_task_not_waiting_or_unknown_prints_the_refusal(project, monkeypatch, capsys):
    svc = _fake_cli(project, monkeypatch)
    svc.add(1, 'CSV export')                                  # still planning: no plan waits
    assert at.main(['decide', '1', 'approve', '--project', str(project)]) == 1
    assert 'Not done: ' in capsys.readouterr().out and svc.tasks[1]['status'] == 'planning'
    assert at.main(['decide', '9', 'approve', '--project', str(project)]) == 1
    assert 'Not done: ' in capsys.readouterr().out


@pytest.mark.parametrize('argv', [['decide', '1', 'okay'], ['decide', '1'], ['decide']])
def test_decide_needs_a_known_decision_word(project, monkeypatch, capsys, argv):
    svc = _fake_cli(project, monkeypatch)
    _waiting(svc)
    with pytest.raises(SystemExit) as e:
        at.main(argv + ['--project', str(project)])
    assert e.value.code == 2 and svc.decisions == [] and svc.tasks[1]['status'] == 'plan_review'


def test_decide_against_the_real_service(project, central, team, env):
    connect(project)
    n = at.propose(project, 'CSV export', '## Intent\nCSV.', ITEMS, env, central.send)
    task = at.decide(project, n, 'approve', '', env, central.send)
    assert task['status'] == 'approved' and server_task(central, n)['status'] == 'approved'
    with pytest.raises(at.TaskError, match='no plan waiting'):
        at.decide(project, n, 'approve', '', env, central.send)
