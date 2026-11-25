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


# --- H9 guardrails: daily spend, usage limit, checkout lock -------------------------------------------------------------

def spend(p, now):
    return json.loads(aw.spend_path(p, now).read_text(encoding='utf-8'))


class CostlyClaude(FakeClaude):
    """FakeClaude whose builds report a cost too (0.7 USD per build attempt)."""

    def __call__(self, cmd, cwd):
        code, out = super().__call__(cmd, cwd)
        if self.roles[-1] == 'build':
            out += '\n' + json.dumps({'type': 'result', 'result': 'done', 'total_cost_usd': 0.7})
        return code, out


def test_planning_and_build_costs_both_count_in_the_days_spend_file(watched, central, team, env):
    n = add_task(central, team)['number']
    claude, clock = CostlyClaude(), Clock()
    assert watch(watched, central, env, clock, claude, max_steps=1) == 0          # plans it: planner and spec check
    day = spend(watched, clock.now)
    assert day['date'] == aw.local_day(clock.now) and day['spent_usd'] == pytest.approx(0.15)
    assert [(c['task'], c['action'], c['cost_usd']) for c in day['calls']] == [(n, 'plan', 0.1), (n, 'plan', 0.05)]
    approve(central, team, n)
    assert watch(watched, central, env, clock, claude, max_steps=1) == 0          # builds it
    day = spend(watched, clock.now)
    builds = [c for c in day['calls'] if c['action'] == 'build']
    assert builds and all(c['task'] == n and c['cost_usd'] == 0.7 for c in builds)
    assert day['spent_usd'] == pytest.approx(0.15 + 0.7 * len(builds))
    assert aw.spent_today(watched, clock.now) == pytest.approx(day['spent_usd'])
    assert server_task(central, n)['status'] == 'done'


def test_the_daily_cap_pauses_until_local_midnight(watched, central, team, env, capsys):
    n = add_task(central, team)['number']
    clock = Clock()
    midnight = aw.next_midnight(clock.now)
    clock.now = midnight - 600                                     # ten minutes before local midnight
    aw.add_spend(watched, clock.now, 20.0, 99, 'build')            # the default cap (20 USD) is reached today
    seen = []
    clock.on_sleep = lambda: seen.append((watch_file(watched)['state'], list(central.calls)))
    claude = FakeClaude()
    assert watch(watched, central, env, clock, claude, max_steps=1) == 0
    assert clock.now >= midnight and sum(clock.slept) == pytest.approx(600)
    assert seen and all(state == 'paused_daily_budget' and calls and all('/runners/beat?' in c[1] for c in calls)
                        for state, calls in seen)                  # nothing claimed: only presence beats
    assert 'daily budget reached (20.00 of 20 USD today): paused until local midnight' in capsys.readouterr().out
    assert claude.roles == ['plan', 'spec'] and server_task(central, n)['status'] == 'plan_review'
    assert aw.local_day(clock.now) != aw.local_day(midnight - 600)
    assert spend(watched, clock.now)['spent_usd'] == pytest.approx(0.15)          # a new day starts a new file


def test_spend_below_the_cap_does_not_pause(watched, central, env):
    clock = Clock()
    aw.add_spend(watched, clock.now, 19.99)
    assert watch(watched, central, env, clock, max_steps=1) == 0
    assert watch_file(watched)['state'] == 'idle' and sum(clock.slept) == POLL


def test_a_usage_limit_waits_for_the_reset(watched, central, team, env, capsys):
    add_task(central, team)
    clock = Clock()
    reset = int(clock.now) + 600

    def limited(cmd, cwd):
        return 1, json.dumps({'type': 'result', 'is_error': True, 'total_cost_usd': 0.02,
                              'result': f'Claude AI usage limit reached|{reset}'})

    assert watch(watched, central, env, clock, limited, max_steps=1) == 0
    assert sum(clock.slept) == pytest.approx(660)                                  # the reset plus a minute
    beat = watch_file(watched)
    assert beat['state'] == 'waiting_usage_limit' and beat['reset'] == aw._iso(reset)
    assert 'usage limit reached' in capsys.readouterr().out
    assert spend(watched, clock.now)['spent_usd'] == pytest.approx(0.02)


def test_a_build_stopped_by_the_usage_limit_waits_before_the_next_claim(watched, central, team, env):
    n = add_task(central, team)['number']
    ar.autotasks.take_next(watched, env, central.send)
    ar.autotasks.post_plan(watched, n, PLAN, ITEMS, env, central.send)
    approve(central, team, n)
    clock = Clock()
    usage_exit = aw._runner().USAGE_LIMIT_EXIT
    assert watch(watched, central, env, clock, build=lambda *a: usage_exit, max_steps=1) == 0
    assert sum(clock.slept) == aw._runner().LIMIT_POLL_MINUTES * 60               # unknown reset: the longest wait
    assert watch_file(watched)['state'] == 'waiting_usage_limit'


def approved(watched, central, team, env):
    n = add_task(central, team)['number']
    ar.autotasks.take_next(watched, env, central.send)
    ar.autotasks.post_plan(watched, n, PLAN, ITEMS, env, central.send)
    approve(central, team, n)
    return n


def test_a_build_that_only_waited_is_reported_as_waiting(watched, central, team, env, capsys):
    n = approved(watched, central, team, env)
    slugs = []
    clock = Clock()
    assert watch(watched, central, env, clock, build=lambda p, slug, *a: slugs.append(slug) or 0, max_steps=1) == 0
    assert (watched / '.auto' / 'pending' / slugs[0]).is_dir()         # the run left its request pending
    out = capsys.readouterr().out
    assert f'task {n} (build): waiting, waits for an earlier branch to be merged' in out and ': done,' not in out
    beat = watch_file(watched)
    assert beat['state'] == 'waiting_for_merge'
    assert beat['last']['ok'] is True and beat['last']['waiting'] is True
    assert beat['last']['detail'] == 'waits for an earlier branch to be merged'
    assert sum(clock.slept) == POLL                                     # one poll before the next step


def test_a_failed_build_with_its_request_pending_is_still_a_failure(watched, central, team, env, capsys):
    n = approved(watched, central, team, env)
    clock = Clock()
    assert watch(watched, central, env, clock, build=lambda *a: 1, max_steps=1) == 0
    assert f'task {n} (build): failed, runner exit 1' in capsys.readouterr().out
    beat = watch_file(watched)
    assert beat['state'] == 'waiting_after_failure'
    assert beat['last']['ok'] is False and 'waiting' not in beat['last']
    assert sum(clock.slept) == POLL


def test_a_build_that_removes_its_request_is_done(watched, central, team, env, capsys):
    import shutil
    n = approved(watched, central, team, env)

    def build(project, slug, *a):
        shutil.rmtree(Path(project) / '.auto' / 'pending' / slug)
        return 0

    result = aw.step(watched, FakeClaude(), env, central.send, build=build)
    assert result['ok'] is True and 'waiting' not in result and result['detail'] == 'runner exit 0'
    clock = Clock()
    approved(watched, central, team, env)
    assert watch(watched, central, env, clock, build=build, max_steps=1) == 0
    out = capsys.readouterr().out
    assert '(build): done, runner exit 0' in out and 'waiting' not in out
    assert clock.slept == [] and n                          # done: the next step comes straight away, no heartbeat


def test_a_usage_limit_with_exit_0_and_the_request_pending_is_not_waiting(watched, central, team, env):
    approved(watched, central, team, env)
    meter = aw.Meter(watched, Clock().time)
    meter.limit = {'reset': None}
    result = aw.step(watched, FakeClaude(), env, central.send, build=lambda *a: 0, meter=meter)
    assert result['ok'] is True and 'waiting' not in result


def test_a_second_watcher_on_the_same_checkout_refuses_to_build(watched, central, team, env, capsys):
    add_task(central, team)
    first = aw._runner().Lock(watched / aw.LOCK_FILE)
    first.acquire()                                                # a live watcher (this process) holds the checkout
    try:
        built = []
        code = watch(watched, central, env, Clock(), build=lambda *a: built.append(a) or 0, max_steps=3)
        assert code == 2 and built == [] and central.calls == []
        assert 'Not started: another watcher holds' in capsys.readouterr().out
    finally:
        first.release()
    assert watch(watched, central, env, Clock(), max_steps=1) == 0               # free again once the first one ends
    assert not (watched / aw.LOCK_FILE).exists()


def test_a_stale_watch_lock_is_cleared(watched, central, env):
    lock = watched / aw.LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text('999999999', encoding='utf-8')                 # a watcher that died
    assert watch(watched, central, env, Clock(), max_steps=1) == 0
    assert watch_file(watched)['state'] == 'idle'


# --- H12 presence: the loop beats its state and obeys pause and stop ----------------------------------------------------

from tests.auto_runner.fake_central import FakeRunners  # noqa: E402

LABEL = 'alok/bhawish-claude-auto-runner'


def presence(central):
    return FakeRunners(central.send)


def watch_via(project, runners, env, clock, claude=None, **kw):
    return aw.watch(project, claude or FakeClaude(), env, runners.send, clock=clock.time, sleep=clock.sleep, **kw)


def claims(runners):
    return runners.order.count('/api/v1/tasks/next')


def test_beats_carry_the_state_task_and_spend(watched, central, team, env):
    n = add_task(central, team)['number']
    runners, clock = presence(central), Clock()
    assert watch_via(watched, runners, env, clock, max_steps=2) == 0       # plans it, then idles
    assert runners.states() == ['watching', f'planning #{n}', 'watching', 'watching']    # the last: mid-way in the idle wait
    assert [b['task'] for b in runners.beats] == [None, n, None, None]
    assert all(b['label'] == LABEL for b in runners.beats)
    assert runners.beats[0]['spend_usd'] == 0 and runners.beats[2]['spend_usd'] == pytest.approx(0.15)
    assert runners.order[:3] == ['beat', '/api/v1/tasks/next', 'beat']      # a beat before each claim, then on claiming
    approve(central, team, n)
    runners.beats.clear()
    assert watch_via(watched, runners, env, clock, max_steps=1) == 0
    assert runners.states()[:2] == ['watching', f'building #{n}'] and runners.beats[1]['task'] == n
    assert server_task(central, n)['status'] == 'done'


def test_a_paused_runner_waits_without_claiming_until_resumed(watched, central, team, env, capsys):
    n = add_task(central, team)['number']
    runners = presence(central)
    runners.control = 'pause'
    seen = []

    def on_sleep():
        seen.append((watch_file(watched)['state'], claims(runners)))
        if len(clock.slept) == 2 * POLL // aw.WAIT_SLICE:                   # two polls paused, then a person resumes it
            runners.control = None

    clock = Clock(on_sleep=on_sleep)
    assert watch_via(watched, runners, env, clock, max_steps=1) == 0
    assert seen[:24] == [('paused', 0)] * 24 and sum(clock.slept[:24]) == 2 * POLL
    assert runners.states()[:3] == ['watching', 'paused', 'paused']         # it says it is paused while it waits
    assert runners.states()[3] == f'planning #{n}' and claims(runners) == 1
    assert server_task(central, n)['status'] == 'plan_review'
    out = capsys.readouterr().out
    assert out.count('paused from the service') == 1 and 'resumed from the service' in out


def test_stop_from_the_service_ends_the_loop_cleanly(watched, central, team, env, capsys):
    add_task(central, team)
    runners = presence(central)
    runners.control = 'stop'
    assert watch_via(watched, runners, env, Clock()) == 0                   # no max_steps: the stop ends it
    assert claims(runners) == 0 and runners.states() == ['watching']
    assert watch_file(watched)['state'] == 'stopped' and 'stopped from the service' in capsys.readouterr().out
    assert not (watched / aw.LOCK_FILE).exists()                            # the checkout lock is released


def test_stop_during_a_step_lets_the_step_finish_first(watched, central, team, env):
    n = add_task(central, team)['number']
    runners = presence(central)

    class StopWhilePlanning(FakeClaude):
        def __call__(self, cmd, cwd):
            runners.control = 'stop'                                        # a person presses Stop mid-plan
            return super().__call__(cmd, cwd)

    assert watch_via(watched, runners, env, Clock(), StopWhilePlanning()) == 0
    assert server_task(central, n)['status'] == 'plan_review'               # the plan was still posted
    assert claims(runners) == 1 and runners.states() == ['watching', f'planning #{n}', 'watching']
    assert watch_file(watched)['state'] == 'stopped' and watch_file(watched)['last']['ok'] is True


def test_stop_is_obeyed_during_the_daily_budget_pause(watched, central, env):
    clock, runners = Clock(), presence(central)
    aw.add_spend(watched, clock.now, 20.0)
    runners.control = 'stop'
    assert watch_via(watched, runners, env, clock) == 0
    assert runners.states() == ['paused: daily budget reached'] and claims(runners) == 0
    assert clock.slept == [] and watch_file(watched)['state'] == 'stopped'


def test_a_failed_beat_is_reported_once_and_the_loop_carries_on(watched, central, team, env, capsys):
    n = add_task(central, team)['number']
    runners = presence(central)
    runners.refuse = 403
    assert watch_via(watched, runners, env, Clock(), max_steps=2) == 0
    assert claims(runners) == 2 and server_task(central, n)['status'] == 'plan_review'
    out = capsys.readouterr().out
    assert out.count('could not report to the service') == 1 and '403' in out
    runners.refuse, runners.down = None, True
    assert watch_via(watched, runners, env, Clock(), max_steps=1) == 0
    assert claims(runners) == 3 and watch_file(watched)['state'] == 'idle'


def test_presence_and_stop_through_the_real_service(watched, central, team, env):
    n = add_task(central, team)['number']
    clock = Clock()
    assert watch(watched, central, env, clock, max_steps=1) == 0
    row = central.conn.query_one('SELECT * FROM runner WHERE label = ?', (LABEL,))
    assert row['state'] == f'planning #{n}' and row['task_number'] == n
    central.conn.execute("UPDATE runner SET control = 'stop' WHERE label = ?", (LABEL,))
    central.conn.commit()
    before = len(central.calls)
    assert watch(watched, central, env, clock) == 0                         # a person pressed Stop on the pages
    assert [c[1].split('?')[0] for c in central.calls[before:]] == ['https://central.test/api/v1/runners/beat']
    assert watch_file(watched)['state'] == 'stopped'


def test_wake_polls_at_once(watched, central, team, env, capsys):
    """An idle watcher beats during its wait; a `wake` from the Supervisor ends the wait and claims at once."""
    runners = presence(central)
    seen = {}

    def on_sleep():
        if len(clock.slept) == 2:                       # ten seconds into the idle wait a task is ready, and it is woken
            seen['n'] = add_task(central, team)['number']
            runners.control = 'wake'

    clock = Clock(on_sleep=on_sleep)
    assert watch_via(watched, runners, env, clock, max_steps=2) == 0
    assert sum(clock.slept) == aw.IDLE_BEAT < POLL      # not the whole poll
    assert claims(runners) == 2 and server_task(central, seen['n'])['status'] == 'plan_review'
    assert runners.states()[:3] == ['watching', 'watching', f'planning #{seen["n"]}']
    assert 'woken from the service' in capsys.readouterr().out


def test_idle_wait_without_a_wake_still_lasts_one_poll(watched, central, env):
    runners, clock = presence(central), Clock()
    assert watch_via(watched, runners, env, clock, max_steps=1) == 0
    assert sum(clock.slept) == POLL and claims(runners) == 1
    assert runners.states() == ['watching', 'watching']      # one beat in the middle of the wait


def test_a_stop_seen_in_the_idle_wait_ends_the_loop(watched, central, env):
    runners, clock = presence(central), Clock(on_sleep=lambda: setattr(runners, 'control', 'stop'))
    assert watch_via(watched, runners, env, clock) == 0
    assert claims(runners) == 1 and watch_file(watched)['state'] == 'stopped' and sum(clock.slept) == aw.IDLE_BEAT
