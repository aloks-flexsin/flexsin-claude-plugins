"""auto-runner plugin: the watch loop (bin/autowatch.py, decision record 0009) against the real central app (Flask test
client, no socket), the real runner in a git project, a fake claude (planner, spec check and builder) and a fake clock:
plans a new task, builds an approved one, writes the idle heartbeat, stops on STOP, carries on after a failed build."""
import importlib.util
import json
from pathlib import Path

import pytest

from tests.auto_runner.test_central_tasks import ITEMS, add_task, connect, env, server_task, team  # noqa: F401  (fixtures)
from tests.auto_runner.test_init_central import BASE, central  # noqa: F401  (central is a fixture)
from tests.auto_runner.test_runner import ar, picking_agent, project  # noqa: F401  (project is a fixture)

BIN = Path(__file__).resolve().parents[2] / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autorunner_watch_t', BIN / 'autowatch.py')
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)

POLL = 60
PLAN = '## Intent\nCSV export.\n\n## Queue items\n' + ITEMS + '\n## Needs a person\nNone.\n'


class Clock:
    """Fake time: sleep moves it forward; `on_sleep` runs after each sleep (to drop a STOP file mid-wait)."""

    def __init__(self, on_sleep=None):
        self.now, self.slept, self.on_sleep = 1_800_000_000.0, [], on_sleep

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


class FakeClaude:
    """The planner gets PLAN, the spec check no findings, the builder (prompt `Do ONLY item X`) the picking agent."""

    def __init__(self):
        self.build, self.roles = picking_agent(), []

    def __call__(self, cmd, cwd):
        text = cmd[cmd.index('-p') + 1]
        if 'Do ONLY item ' in text:
            self.roles.append('build')
            return self.build(cmd, cwd)
        if text.startswith('You review a work plan'):
            self.roles.append('spec')
            return 0, json.dumps({'type': 'result', 'total_cost_usd': 0.05,
                                  'result': json.dumps({'schema': 'spec.v1', 'ok': True, 'findings': []})})
        self.roles.append('plan')
        return 0, json.dumps({'type': 'result', 'result': PLAN, 'total_cost_usd': 0.1})


@pytest.fixture
def watched(project, central, team, env, monkeypatch):
    connect(project)
    cfg_path = project / '.claude' / 'auto.json'
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    cfg['watch'] = {'poll_seconds': POLL}
    cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
    ar.git(project, 'commit', '-q', '-am', 'watch settings')
    for k, v in env.items():                     # the runner names its branch from these
        monkeypatch.setenv(k, v)
    return project


def watch(project, central, env, clock, claude=None, **kw):
    return aw.watch(project, claude or FakeClaude(), env, central.send, clock=clock.time, sleep=clock.sleep, **kw)


def watch_file(p):
    return json.loads((p / '.auto' / 'run' / 'watch.json').read_text(encoding='utf-8'))


def approve(central, team, number):
    from central import tasks
    tasks.decide(central.conn, central.project, server_task(central, number), team['dev'], 'approve', '', team['now'])


def test_plans_a_new_task_then_idles(watched, central, team, env, capsys):
    n = add_task(central, team)['number']
    claude, clock = FakeClaude(), Clock()
    assert watch(watched, central, env, clock, claude, max_steps=2) == 0
    row = server_task(central, n)
    assert row['status'] == 'plan_review' and '**T1 Export.**' in row['items']
    assert claude.roles == ['plan', 'spec']
    assert f'task {n} (plan): done, posted' in capsys.readouterr().out
    beat = watch_file(watched)
    assert beat['state'] == 'idle' and beat['last']['task'] == n and beat['last']['ok'] is True


def test_builds_an_approved_task(watched, central, team, env, capsys):
    n = add_task(central, team)['number']
    ar.autotasks.take_next(watched, env, central.send)
    ar.autotasks.post_plan(watched, n, PLAN, ITEMS, env, central.send)
    approve(central, team, n)
    claude = FakeClaude()
    assert watch(watched, central, env, Clock(), claude, max_steps=1) == 0
    row = server_task(central, n)
    assert row['status'] == 'done' and row['branch'].startswith('alok/bhawish-claude-auto-runner/')
    assert 'build' in claude.roles and 'plan' not in claude.roles
    assert f'task {n} (build): done, runner exit 0' in capsys.readouterr().out


def test_idle_writes_the_heartbeat_and_sleeps_one_poll(watched, central, env):
    clock = Clock()
    start = clock.now
    assert watch(watched, central, env, clock, max_steps=1) == 0
    beat = watch_file(watched)
    assert beat['state'] == 'idle' and beat['at'] == aw._iso(start) and 'last' not in beat
    assert sum(clock.slept) == POLL and max(clock.slept) <= aw.WAIT_SLICE


def test_stop_file_before_starting_claims_nothing(watched, central, team, env, capsys):
    add_task(central, team)
    (watched / '.auto' / 'STOP').write_text('', encoding='utf-8')
    assert watch(watched, central, env, Clock()) == 0
    assert central.calls == [] and watch_file(watched)['state'] == 'stopped'
    assert 'STOP file found' in capsys.readouterr().out
    assert (watched / '.auto' / 'STOP').exists()            # left for the person, like the runner's own check


def test_stop_file_during_the_idle_wait_ends_the_loop(watched, central, env):
    stop = watched / '.auto' / 'STOP'
    clock = Clock(on_sleep=lambda: stop.write_text('', encoding='utf-8'))
    assert watch(watched, central, env, clock) == 0          # no max_steps: only STOP ends it
    assert clock.slept == [aw.WAIT_SLICE] and watch_file(watched)['state'] == 'stopped'


def test_a_failed_build_does_not_end_the_loop(watched, central, team, env, capsys):
    n = add_task(central, team)['number']
    ar.autotasks.take_next(watched, env, central.send)
    ar.autotasks.post_plan(watched, n, PLAN, ITEMS, env, central.send)
    approve(central, team, n)
    attempts = []

    def build(project, slug, claude, env, send):
        attempts.append(slug)
        if len(attempts) == 1:
            raise RuntimeError('the worktree could not be made')
        return aw._default_build(project, slug, claude, env, send)

    clock = Clock()
    assert watch(watched, central, env, clock, build=build, max_steps=3) == 0
    out = capsys.readouterr().out
    assert f'task {n} (build): failed, RuntimeError: the worktree could not be made' in out
    assert f'task {n} (build): done, runner exit 0' in out
    assert len(attempts) == 2 and server_task(central, n)['status'] == 'done'
    assert sum(clock.slept) == 2 * POLL                     # one poll after the failure, one when idle at the end
    assert watch_file(watched)['state'] == 'idle'


def test_an_unreachable_service_is_reported_and_retried(watched, central, team, env, capsys):
    central.down = True
    clock = Clock()
    assert watch(watched, central, env, clock, max_steps=2) == 0
    assert capsys.readouterr().out.count('claim: failed, could not claim a task') == 2
    assert watch_file(watched)['state'] == 'waiting_after_failure' and sum(clock.slept) == 2 * POLL
