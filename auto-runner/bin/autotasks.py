"""
Central tasks from the plugin's side (decision record 0007, step 4). Needs the central service and a personal token
(sign in with /auto-runner:central): a task is someone's, and the service knows whose from the token.

    python autotasks.py next   [--project DIR]                      # claim my next task; print what to do (plan or build)
    python autotasks.py plan N --plan FILE --items FILE [--project DIR]   # post the plan of task N; it waits for approval
    python autotasks.py start N [--project DIR]                     # an approved task N -> .auto/pending/task-N-<brief>/
    python autotasks.py record --slug SLUG --title T --plan FILE --items FILE [--project DIR]
                                                                    # a task planned in the terminal, recorded as building
    python autotasks.py list   [--project DIR]                      # every task of the project and who works on it

The claim of a task this computer took is kept in `.auto/run/tasks/<N>.json` (git-ignored). A request made from a task
carries `task.json`; when the runner adds that request to its queue it becomes `.auto/run/current-task.json`, and the
runner then beats the claim before every iteration (`beat`: a released or reassigned task stops the run, reason
`claim_lost`) and reports at the end (`finish`: done when the queue is empty after a clean run, else stopped with the
reason). Nothing here prints a token.
"""
import argparse
import importlib.util
import json
import os
import re
import sys
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
autosafe = _load('autorunner_safe', 'autosafe.py')
autodeliver = _load('autorunner_deliver', 'autodeliver.py')

CURRENT = Path('run') / 'current-task.json'
CLAIMS = Path('run') / 'tasks'


class TaskError(RuntimeError):
    pass


def transport(project, env=None, send=None):
    """-> HttpTransport for this project with the personal token. TaskError when central is off or there is no
    personal token (a project token cannot take tasks: they are someone's)."""
    cfg = autocfg.load(project)
    if not autosafe.central_enabled(cfg):
        raise TaskError('this project is not connected to the central service: run /auto-runner:central first')
    token, _ = autosafe.central_token(cfg, env)
    if not token.startswith(autosafe.PERSONAL_TOKEN_PREFIX):
        raise TaskError('tasks need you signed in (a personal token): run /auto-runner:central and sign this computer in')
    return autodeliver.HttpTransport(cfg['central']['url'], token, send=send, project=cfg['central']['project'])


def _call(t, method, path, payload=None):
    try:
        return t.request(method, path, payload)
    except autodeliver.TransportRefused as e:
        raise TaskError(str(e)) from None


def _auto(project):
    return Path(project).resolve() / '.auto'


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def _claim(project, number):
    try:
        return json.loads((_auto(project) / CLAIMS / f'{number}.json').read_text(encoding='utf-8'))['claim']
    except (OSError, ValueError, KeyError):
        raise TaskError(f'this computer holds no claim on task {number}: take it with "next" first') from None


def runner_label(env=None):
    return autosafe.runner_name(env) or 'auto-runner'


def take_next(project, env=None, send=None):
    """Claim my next task -> the reply ({'task', 'claim', 'action'} or {'task': None}); the claim is saved."""
    reply = _call(transport(project, env, send), 'POST', '/api/v1/tasks/next', {'runner': runner_label(env)})
    task = reply.get('task')
    if task:
        _save(_auto(project) / CLAIMS / f'{task["number"]}.json',
              {'number': task['number'], 'claim': reply['claim'], 'title': task['title'], 'action': reply.get('action')})
    return reply


def post_plan(project, number, plan_text, items_text, env=None, send=None):
    return _call(transport(project, env, send), 'POST', f'/api/v1/tasks/{int(number)}/plan',
                 {'claim': _claim(project, number), 'plan': plan_text, 'items': items_text})['task']


def brief(title):
    return re.sub(r'[^a-z0-9]+', '-', (title or '').lower()).strip('-')[:30].strip('-') or 'task'


def start(project, number, env=None, send=None):
    """An approved task this computer claimed -> its request folder slug: `.auto/pending/task-<N>-<brief>/` with the
    approved items, the plan and task.json, ready for `auto_run.py --enqueue <slug>`."""
    claim = _claim(project, number)
    task = _call(transport(project, env, send), 'GET', f'/api/v1/tasks/{int(number)}')['task']
    if task['status'] != 'building' or not task['items'].strip():
        raise TaskError(f'task {number} is {task["status"]}, not approved for building')
    slug = f'task-{int(number)}-{brief(task["title"])}'
    folder = _auto(project) / 'pending' / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'items.md').write_text(task['items'].rstrip() + '\n', encoding='utf-8')
    (folder / 'plan.md').write_text(task['plan'].rstrip() + '\n', encoding='utf-8')
    _save(folder / 'task.json', {'number': int(number), 'claim': claim})
    return slug


def record(project, slug, title, plan_text, items_text, env=None, send=None):
    """A task planned and approved in the terminal: recorded on the service as building, claimed by this runner, and
    tied to the request folder `.auto/pending/<slug>` (task.json) -> its number."""
    reply = _call(transport(project, env, send), 'POST', '/api/v1/tasks',
                  {'runner': runner_label(env), 'title': title, 'plan': plan_text, 'items': items_text})
    number = reply['task']['number']
    _save(_auto(project) / 'pending' / slug / 'task.json', {'number': number, 'claim': reply['claim']})
    return number


def listing(project, env=None, send=None):
    return _call(transport(project, env, send), 'GET', '/api/v1/tasks')['tasks']


# ---------- used by the runner ----------

def adopt(auto_dir, request_dir):
    """The runner adds a request to its queue: a task.json in it becomes the run's current task."""
    src = Path(request_dir) / 'task.json'
    if src.exists():
        _save(Path(auto_dir) / CURRENT, json.loads(src.read_text(encoding='utf-8')))


def run_task(auto_dir, request_dir, new_branch):
    """The central task a run is about to work on, known before it starts -> its number or None: the request it takes
    on a new branch (its task.json), else the task the branch is already working on."""
    if new_branch and request_dir is not None:
        try:
            number = json.loads((Path(request_dir) / 'task.json').read_text(encoding='utf-8')).get('number')
            return number if isinstance(number, int) else None
        except (OSError, ValueError, AttributeError):
            return None
    task = current(auto_dir)
    return task['number'] if task else None


def current(auto_dir):
    try:
        data = json.loads((Path(auto_dir) / CURRENT).read_text(encoding='utf-8'))
        return data if isinstance(data.get('number'), int) and data.get('claim') else None
    except (OSError, ValueError, AttributeError):
        return None


def beat(t, auto_dir):
    """-> 'none' (no current task), 'ok', 'lost' (released, reassigned or expired: stop) or 'unavailable'."""
    task = current(auto_dir)
    if task is None:
        return 'none'
    try:
        t.request('POST', f'/api/v1/tasks/{task["number"]}/heartbeat', {'claim': task['claim']})
        return 'ok'
    except (RuntimeError, ConnectionError) as e:        # TransportRefused / TransportUnavailable, whichever module copy
        if getattr(e, 'status', None) == 409:
            (Path(auto_dir) / CURRENT).unlink(missing_ok=True)
            return 'lost'
        return 'unavailable'


def finish(t, auto_dir, done, branch, pr_url, note):
    """Report the end of a run on the current task -> a line to say, or None. Done clears the current task."""
    task = current(auto_dir)
    if task is None:
        return None
    try:
        t.request('POST', f'/api/v1/tasks/{task["number"]}/report',
                  {'claim': task['claim'], 'result': 'done' if done else 'stopped', 'branch': branch, 'pr_url': pr_url,
                   'note': note})
    except (RuntimeError, ConnectionError) as e:        # TransportRefused / TransportUnavailable, whichever module copy
        if getattr(e, 'status', None) == 409:
            (Path(auto_dir) / CURRENT).unlink(missing_ok=True)
        return f'task {task["number"]}: could not report to the central service ({e})'
    if done:
        (Path(auto_dir) / CURRENT).unlink(missing_ok=True)
        return f'task {task["number"]} reported done'
    return f'task {task["number"]} reported stopped ({note})'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=('next', 'plan', 'start', 'record', 'list'))
    ap.add_argument('number', nargs='?', type=int)
    ap.add_argument('--project', default=os.environ.get('CLAUDE_PROJECT_DIR') or '.')
    ap.add_argument('--plan', default='')
    ap.add_argument('--items', default='')
    ap.add_argument('--slug', default='')
    ap.add_argument('--title', default='')
    a = ap.parse_args(argv)
    read = lambda p: Path(p).read_text(encoding='utf-8') if p else ''   # noqa: E731
    try:
        if a.cmd == 'next':
            reply = take_next(a.project)
            task = reply.get('task')
            if not task:
                print('No task waiting for you in this project. Add one on the central page (Tasks) or give /auto-runner:auto a task.')
                return 0
            print(f'Task {task["number"]}: {task["title"]}\nAction: {reply["action"]}\n')
            for key in ('description', 'comment', 'plan', 'items'):
                if task.get(key):
                    print(f'--- {key} ---\n{task[key]}\n')
        elif a.cmd == 'plan':
            task = post_plan(a.project, a.number, read(a.plan), read(a.items))
            print(f'Task {task["number"]}: the plan waits for approval on the central page (Tasks).')
        elif a.cmd == 'start':
            print(start(a.project, a.number))
        elif a.cmd == 'record':
            print(f'Recorded as task {record(a.project, a.slug, a.title, read(a.plan), read(a.items))}.')
        else:
            for t in listing(a.project):
                who = f' (worked by {t["runner"]})' if t.get('runner') else ''
                print(f'#{t["number"]} [{t["status"]}] {t["title"]} - {t["assignee"]}{who}')
    except (TaskError, autodeliver.TransportUnavailable, OSError) as e:
        print(f'Not done: {e}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
