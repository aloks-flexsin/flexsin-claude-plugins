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
Daily spend, the usage-limit wait, the checkout lock and presence on the service come in later steps.
Exit 0 = stopped by the STOP file. Stdlib only.
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
WAIT_SLICE = 5          # seconds: how often a wait looks for the STOP file


def _iso(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).isoformat(timespec='seconds')


def heartbeat(project, state, now, last=None):
    """Write .auto/run/watch.json: the loop's state, the time and the last step's outcome."""
    path = Path(project) / WATCH_FILE
    data = {'state': state, 'at': _iso(now), 'pid': os.getpid()}
    if last:
        data['last'] = last
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


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


def step(project, claude=None, env=None, send=None, build=None):
    """Claim the next task and plan or build it -> the outcome {'task', 'action', 'ok', 'detail'}, or None when there
    is nothing to do. Never raises for a failed task: the failure is the outcome."""
    reply = autotasks.take_next(project, env, send)
    task = reply.get('task')
    if not task:
        return None
    number, action = task['number'], reply.get('action') or 'plan'
    out = {'task': number, 'action': action, 'title': task.get('title', ''), 'ok': False, 'detail': ''}
    try:
        if action == 'plan':
            r = autoplan.plan(project, number, claude, env, send)
            out.update(ok=r['status'] == 'posted', detail=r['status'] + (f': {r["reason"]}' if r['reason'] else ''))
        else:
            slug = autotasks.start(project, number, env, send)
            code = (build or _default_build)(project, slug, claude, env, send)
            out.update(ok=code == 0, detail=f'runner exit {code}')
    except Exception as e:                      # one failed task never ends the loop
        out['detail'] = f'{type(e).__name__}: {e}'
    return out


def watch(project, claude=None, env=None, send=None, build=None, clock=time.time, sleep=time.sleep, out=None,
          max_steps=None):
    """The loop -> 0 when the STOP file ends it. `claude`, `send` and `build(project, slug, claude, env, send) -> exit`
    replace the CLI, the service and the runner in tests; `clock`/`sleep` the time; `max_steps` bounds a test run."""
    project = Path(project).resolve()
    stop = project / STOP_FILE
    poll = float(autocfg.watch(autocfg.load(project))['poll_seconds'])
    last, steps = None, 0

    def say(text):
        print(f'[watch] {text}', file=out or sys.stdout, flush=True)

    def wait(seconds):
        end = clock() + seconds
        while not stop.exists() and clock() < end:
            sleep(min(WAIT_SLICE, end - clock()))

    say(f'watching {project} (every {poll:g} s when idle; create {STOP_FILE} to stop)')
    while True:
        if stop.exists():
            heartbeat(project, 'stopped', clock(), last)
            say('STOP file found: stopping.')
            return 0
        if max_steps is not None and steps >= max_steps:
            return 0
        steps += 1
        try:
            result = step(project, claude, env, send, build)
        except (autotasks.TaskError, autodeliver.TransportUnavailable, autocfg.ConfigError, OSError) as e:
            result = {'task': None, 'action': 'claim', 'ok': False, 'detail': f'could not claim a task: {e}'}
        if result is None:
            heartbeat(project, 'idle', clock(), last)
            wait(poll)
            continue
        last = {**result, 'at': _iso(clock())}
        what = f'task {result["task"]} ({result["action"]})' if result['task'] else result['action']
        say(f'{what}: {"done" if result["ok"] else "failed"}, {result["detail"]}')
        if not result['ok']:
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
    sys.exit(main())
