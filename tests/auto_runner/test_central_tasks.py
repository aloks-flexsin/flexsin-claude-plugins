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
    assert server_task(central, n)['status'] == 'building'
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
    tasks.release(central.conn, central.project, server_task(central, n), None, team['now'])
    assert ar.run(project, max_iterations=5, claude=picking_agent(), transport=transport, allow_dirty=True) == 1
    assert 'was released, reassigned or its claim expired' in capsys.readouterr().out
    events = [json.loads(line) for line in (project / '.auto' / 'events.jsonl').read_text().splitlines()]
    assert events[-1]['type'] == 'run_end' and events[-1]['detail']['reason'] == 'claim_lost'
