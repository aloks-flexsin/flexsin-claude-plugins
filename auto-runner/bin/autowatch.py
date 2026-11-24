"""
Watch mode, the loop (decision record 0009): take this person's central tasks one after another, without a person at
the terminal.

    python autowatch.py [--project DIR]

Each step:
1. Stops when `.auto/STOP` exists (checked before every claim and while waiting; the file is left for the person to
   delete, so a second watcher started by mistake stops too).
2. Claims the next task (`autotasks.take_next`). The service says what to do with it:
   - `plan`: the headless planner (`autoplan.plan`) plans it and posts the plan; it then waits for approval on the page.
   - `build`: the approved task becomes a request folder (`autotasks.start`) and the runner works it
     (`auto_run.run(..., enqueue=<slug>)`), which beats the claim and reports the task done or stopped itself.
3. Reports: one line on stdout and the step's outcome under `last` in `.auto/run/watch.json`.
When there is nothing to do it writes the local heartbeat `.auto/run/watch.json` (`state`, `at`, `last`) and waits
`watch.poll_seconds` (default 300). A failed step (planner refused, a build that stopped or crashed, the service
unreachable) is reported and the loop waits one poll before the next claim: one failed task never ends the loop.

Guardrails:
- Daily spend: every `claude -p` the watcher starts (planner, spec check and builds) adds its `total_cost_usd` (from the
  CLI, never estimated) to `.auto/run/spend-<local date of this computer>.json`. Before each claim, when the day's spend
  has reached `watch.daily_budget_usd` (default 20), the loop pauses until local midnight. A step that has started
  finishes: a plan is bounded by `watch.plan_budget_usd`, a build attempt by its own `--max-budget-usd`.
- Usage limit: when a `claude -p` output shows Claude's usage limit (`auto_run.usage_limit`) or a build exits 75, the
  loop waits for the reset plus a minute, at most `auto_run.LIMIT_POLL_MINUTES` (an unknown reset waits that long), then
  claims again.
- Checkout lock: `.auto/run/watch.lock` (`auto_run.Lock`, holding the process id; a stale one is cleared) so only one
  watcher, and so one build, runs per checkout. A second watcher refuses to start (exit 2).
Presence on the service (`POST /api/v1/runners/beat`, personal token, label = `autotasks.runner_label`): before every
claim the loop beats `watching` (or `paused`), and once a task is claimed it beats `planning #N` / `building #N` with the
task number; each beat carries the day's spend. The reply's `control` is obeyed between steps: `pause` waits one poll
without claiming (beating `paused` each poll) until a person resumes it, `stop` ends the loop cleanly (exit 0, heartbeat
`stopped`). A step that has started always finishes. During the daily-budget pause it beats `paused: daily budget
reached` each poll (so the pages do not show it silent) and still obeys stop. A beat that fails (service unreachable,
refused) is reported once and never stops the loop: the runner then carries on as if nobody had asked it to pause.
Exit 0 = stopped (STOP file or the service's stop), 2 = not started. Stdlib only.
"""
import argparse
import datetime
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


autocfg = _load('autorunner_cfg', 'autocfg.py')
autotasks = _load('autorunner_tasks', 'autotasks.py')
autoplan = _load('autorunner_plan', 'autoplan.py')
autodeliver = autotasks.autodeliver

WATCH_FILE = autoplan.WATCH_FILE
STOP_FILE = Path('.auto') / 'STOP'
LOCK_FILE = Path('.auto') / 'run' / 'watch.lock'
SPEND_DIR = Path('.auto') / 'run'
WAIT_SLICE = 5          # seconds: how often a wait looks for the STOP file


def _iso(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).isoformat(timespec='seconds')


def _runner():
    return _load('autorunner_run', 'auto_run.py')


def local_day(now):
    """The local date of this computer at epoch `now`, as YYYY-MM-DD."""
    return datetime.datetime.fromtimestamp(now).date().isoformat()


def next_midnight(now):
    """Epoch seconds of the next local midnight after `now`."""
    day = datetime.datetime.fromtimestamp(now).date() + datetime.timedelta(days=1)
    return datetime.datetime.combine(day, datetime.time()).timestamp()


def spend_path(project, now):
    return Path(project) / SPEND_DIR / f'spend-{local_day(now)}.json'


def spent_today(project, now):
    """USD the watcher's `claude -p` calls reported on this local day (0 when the file is missing or unreadable)."""
    try:
        return float(json.loads(spend_path(project, now).read_text(encoding='utf-8'))['spent_usd'])
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0


def add_spend(project, now, cost, task=None, action=None):
    """Add one call's cost to today's spend file -> the day's new total."""
    path = spend_path(project, now)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        data['spent_usd'] = float(data['spent_usd'])
        if not isinstance(data.get('calls'), list):
            data['calls'] = []
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        data = {'date': local_day(now), 'spent_usd': 0.0, 'calls': []}
    data['spent_usd'] = round(data['spent_usd'] + cost, 6)
    data['calls'].append({'at': _iso(now), 'task': task, 'action': action, 'cost_usd': cost})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    return data['spent_usd']


class Meter:
    """Wraps the `claude(cmd, cwd)` the planner and the runner call: adds each call's cost to the day's spend file and
    remembers a usage limit seen in its output."""

    def __init__(self, project, clock):
        self.project, self.clock, self.limit = project, clock, None

    def wrap(self, claude, task, action):
        def counted(cmd, cwd):
            code, out = claude(cmd, cwd)
            cost = autoplan.parse_cost(out)
            if cost:
                add_spend(self.project, self.clock(), cost, task, action)
            now = datetime.datetime.fromtimestamp(self.clock()).astimezone()
            hit = _runner().usage_limit(out, now=now)
            if hit:
                self.limit = hit
            return code, out
        return counted


def heartbeat(project, state, now, last=None, **extra):
    """Write .auto/run/watch.json: the loop's state, the time and the last step's outcome (and `extra` fields)."""
    path = Path(project) / WATCH_FILE
    data = {'state': state, 'at': _iso(now), 'pid': os.getpid(), **extra}
    if last:
        data['last'] = last
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def beat(project, state, task=None, spend=0.0, env=None, send=None):
    """POST /api/v1/runners/beat -> the service's control: None (carry on), 'pause' or 'stop'. Raises TaskError (not
    connected, no personal token, refused) or TransportUnavailable; the loop reports those and carries on."""
    t = autotasks.transport(project, env, send)
    reply = autotasks._call(t, 'POST', '/api/v1/runners/beat', {'label': autotasks.runner_label(env), 'state': state,
                                                                 'task': task, 'spend_usd': round(spend, 4)})
    control = reply.get('control')
    return control if control in ('pause', 'stop') else None


def _default_build(project, slug, claude, env, send):
    """auto_run.run on the request, with the personal-token transport. The transport is made from auto_run's own copy of
    autodeliver: the runner beats and reports the task only when `isinstance(transport, its HttpTransport)`."""
    auto_run = _load('autorunner_run', 'auto_run.py')
    autotasks.transport(project, env, send)                     # TaskError when central is off or the token is not personal
    cfg = autocfg.load(project)
    token, _ = autotasks.autosafe.central_token(cfg, env)
    transport = auto_run.autodeliver.HttpTransport(cfg['central']['url'], token, send=send,
                                                   project=cfg['central']['project'])
    kw = {'claude': claude} if claude else {}
    return auto_run.run(project, enqueue=slug, transport=transport, **kw)


def step(project, claude=None, env=None, send=None, build=None, meter=None, on_start=None):
    """Claim the next task and plan or build it -> the outcome {'task', 'action', 'ok', 'detail'} (and 'exit' for a
    build), or None when there is nothing to do. Never raises for a failed task: the failure is the outcome. With a
    `meter`, every `claude -p` of the step goes through it (spend and usage limit); `on_start(number, action)` is called
    once the task is claimed, before it is planned or built (the loop beats from it)."""
    reply = autotasks.take_next(project, env, send)
    task = reply.get('task')
    if not task:
        return None
    number, action = task['number'], reply.get('action') or 'plan'
    if on_start:
        on_start(number, action)
    out = {'task': number, 'action': action, 'title': task.get('title', ''), 'ok': False, 'detail': ''}
    if meter:
        base = claude or (autoplan._run_claude if action == 'plan' else _runner().run_claude)
        claude = meter.wrap(base, number, action)
    try:
        if action == 'plan':
            r = autoplan.plan(project, number, claude, env, send)
            out.update(ok=r['status'] == 'posted', detail=r['status'] + (f': {r["reason"]}' if r['reason'] else ''))
        else:
            slug = autotasks.start(project, number, env, send)
            code = (build or _default_build)(project, slug, claude, env, send)
            out.update(ok=code == 0, detail=f'runner exit {code}', exit=code)
    except Exception as e:                      # one failed task never ends the loop
        out['detail'] = f'{type(e).__name__}: {e}'
    return out


def watch(project, claude=None, env=None, send=None, build=None, clock=time.time, sleep=time.sleep, out=None,
          max_steps=None):
    """The loop -> 0 when the STOP file ends it. `claude`, `send` and `build(project, slug, claude, env, send) -> exit`
    replace the CLI, the service and the runner in tests; `clock`/`sleep` the time; `max_steps` bounds a test run."""
    project = Path(project).resolve()
    stop = project / STOP_FILE
    settings = autocfg.watch(autocfg.load(project))
    poll, daily = float(settings['poll_seconds']), float(settings['daily_budget_usd'])
    runner = _runner()
    lock = runner.Lock(project / LOCK_FILE)
    try:
        lock.acquire()
    except runner.RunnerError:
        print(f'[watch] Not started: another watcher holds {LOCK_FILE} on this checkout (only one build runs per '
              f'checkout). Stop it first (create {STOP_FILE}) or, if none is running, delete that file.',
              file=out or sys.stdout, flush=True)
        return 2
    try:
        return _loop(project, stop, poll, daily, runner, claude, env, send, build, clock, sleep, out, max_steps)
    finally:
        lock.release()


def _loop(project, stop, poll, daily, runner, claude, env, send, build, clock, sleep, out, max_steps):
    last, steps, paused, over_budget, beat_problem = None, 0, False, False, None

    def say(text):
        print(f'[watch] {text}', file=out or sys.stdout, flush=True)

    def report(state, task=None):
        """Beat the service -> its control (None when the beat failed: carry on). A failure is said once."""
        nonlocal beat_problem
        try:
            control = beat(project, state, task, spent_today(project, clock()), env, send)
        except (autotasks.TaskError, autodeliver.TransportUnavailable, autocfg.ConfigError, OSError) as e:
            if str(e) != beat_problem:
                say(f'could not report to the service ({e}); carrying on')
            beat_problem = str(e)
            return None
        beat_problem = None
        return control

    def stopped(why):
        heartbeat(project, 'stopped', clock(), last)
        say(why)
        return 0

    def wait(seconds):
        end = clock() + seconds
        while not stop.exists() and clock() < end:
            sleep(min(WAIT_SLICE, end - clock()))

    say(f'watching {project} (every {poll:g} s when idle; create {STOP_FILE} to stop)')
    while True:
        if stop.exists():
            return stopped('STOP file found: stopping.')
        if max_steps is not None and steps >= max_steps:
            return 0
        spent = spent_today(project, clock())
        if spent >= daily:
            if report('paused: daily budget reached') == 'stop':
                return stopped('stopped from the service.')
            until = next_midnight(clock())
            heartbeat(project, 'paused_daily_budget', clock(), last, until=_iso(until), spent_usd=round(spent, 4))
            if not over_budget:
                say(f'daily budget reached ({spent:.2f} of {daily:g} USD today): paused until local midnight')
            over_budget = True
            wait(min(until - clock(), poll))          # beat again each poll so the pages do not show it silent
            continue
        over_budget = False
        control = report('paused' if paused else 'watching')
        if control == 'stop':
            return stopped('stopped from the service.')
        if control == 'pause':
            if not paused:
                say('paused from the service: waiting without taking work until someone resumes it')
            paused = True
            heartbeat(project, 'paused', clock(), last)
            wait(poll)
            continue
        if paused:
            say('resumed from the service')
            paused = False
        steps += 1
        meter = Meter(project, clock)
        try:
            result = step(project, claude, env, send, build, meter,
                          on_start=lambda n, a: report(f'{"planning" if a == "plan" else "building"} #{n}', n))
        except (autotasks.TaskError, autodeliver.TransportUnavailable, autocfg.ConfigError, OSError) as e:
            result = {'task': None, 'action': 'claim', 'ok': False, 'detail': f'could not claim a task: {e}'}
        if result is None:
            heartbeat(project, 'idle', clock(), last)
            wait(poll)
            continue
        last = {**result, 'at': _iso(clock())}
        what = f'task {result["task"]} ({result["action"]})' if result['task'] else result['action']
        say(f'{what}: {"done" if result["ok"] else "failed"}, {result["detail"]}')
        if meter.limit or result.get('exit') == runner.USAGE_LIMIT_EXIT:
            reset = meter.limit['reset'] if meter.limit else None
            cap = runner.LIMIT_POLL_MINUTES * 60
            pause = min(max(reset.timestamp() - clock() + 60, 0), cap) if reset else cap
            when = _iso(reset.timestamp()) if reset else 'an unknown time'
            heartbeat(project, 'waiting_usage_limit', clock(), last, reset=when)
            say(f'usage limit reached (resets {when}): waiting {pause / 60:.0f} minute(s) before the next claim')
            wait(pause)
        elif not result['ok']:
            heartbeat(project, 'waiting_after_failure', clock(), last)
            wait(poll)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='autowatch.py', description=__doc__.strip().splitlines()[0])
    ap.add_argument('--project', default=os.environ.get('CLAUDE_PROJECT_DIR') or '.')
    a = ap.parse_args(argv)
    try:
        return watch(a.project)
    except (autocfg.ConfigError, OSError) as e:
        print(f'Not started: {e}')
        return 2


if __name__ == '__main__':
    _load('autorunner_safe', 'autosafe.py').utf8_console()
    sys.exit(main())
