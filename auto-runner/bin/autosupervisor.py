"""The Supervisor's pass (decision 0010): the plan, merge, order and control steps (`run_pass` does all four). Standard library only; all I/O is injected.

`plan_step(project, http, read, base_ref)` reads the project's state (`GET /api/v1/supervisor/state`), loads the policy from
the base ref through `read(ref, path) -> (text, blob_sha) | None` (autogates.load_policy), runs `plan_gate` on every task
that awaits plan review and has no supervisor decision on its current plan post (`plan_decided` is null), and posts the
verdict to `POST /api/v1/supervisor/tasks/<n>/plan-decision`: approve -> `approve`, changes -> `changes`, person ->
`escalate` (the plan stays for a person). The service sends a third `changes` back as `escalate` and ignores a repeated
decision for a plan it already decided, so running a pass twice never duplicates a decision.

`http(method, path, body=None) -> (status, json)` is the only way out: tests inject a fake, a real run wraps urllib with the
supervisor's personal token (read from the environment, never logged). A failing call is reported in the result and the
pass goes on with the next task.

`autosupervisor.py add --title T --from TEXT --for EMAIL [--description-file F] [--plan F --items F] [--project SLUG]`
adds a task that traces to asked-for work (0010, 6a) through `POST /api/v1/supervisor/tasks`. `--from` says what it comes
from and is required; `--for` is the member whose runner takes it. With a plan and items the task waits for a person to
approve the plan (the supervisor never decides on a task it added); without them it is open and that runner plans it.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

HERE = Path(__file__).resolve().parent


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gates = _load('autorunner_gates', 'autogates.py')
autoforge = _load('autoforge', 'autoforge.py')
autodeploy = _load('autorunner_deploy', 'autodeploy.py')

STATE = '/api/v1/supervisor/state?project=%s'
DECISION = '/api/v1/supervisor/tasks/%d/plan-decision?project=%s'
VERDICT_TO_DECISION = {'approve': 'approve', 'changes': 'changes', 'person': 'escalate'}


def task_autonomy(loaded, state, task):
    """The mode one task is decided in: "auto" only when the repository (`.claude/auto.json` on the base), the central
    project (`state["autonomy"]`, missing = auto) and the task (`manual` unticked) all say auto; else "manual"."""
    if loaded.get('autonomy') == 'manual' or state.get('autonomy') == 'manual' or task.get('manual'):
        return 'manual'
    return 'auto'


def plan_step(project, http, read, base_ref):
    """-> list of {"task", "verdict", "decision", "status", "error"} for the tasks it acted on (none for a quiet pass)."""
    status, state = http('GET', STATE % quote(project, safe=''))
    if status != 200 or not isinstance(state, dict):
        return [{'task': None, 'verdict': None, 'decision': None, 'status': status, 'error': 'could not read the state'}]
    loaded = gates.gate_inputs(read, base_ref)
    out = []
    for task in state.get('tasks', []):
        if task.get('status') != 'plan_review' or task.get('plan_decided'):
            continue
        mode = task_autonomy(loaded, state, task)
        gate = gates.plan_gate(task, loaded['policy'], mode)
        if loaded['error']:
            gate['reasons'] = [loaded['error']] + gate['reasons']
        decision = VERDICT_TO_DECISION[gate['verdict']]
        code, body = http('POST', DECISION % (task['number'], quote(project, safe='')),
                          {'decision': decision, 'reasons': gate['reasons'], 'policy_sha': loaded['sha'],
                           'autonomy': mode})
        ok = code == 200 and isinstance(body, dict)
        out.append({'task': task['number'], 'verdict': gate['verdict'],
                    'decision': body.get('decision') if ok else None, 'status': code,
                    'error': None if ok else (body.get('error') if isinstance(body, dict) else None) or 'refused'})
    return out


MERGE = '/api/v1/supervisor/tasks/%d/merge?project=%s'


def pr_number(url):
    """The pull request number at the end of `https://host/owner/repo/pull/<n>`, or None."""
    parts = urlsplit(url or '').path.rstrip('/').split('/')
    return int(parts[-1]) if len(parts) >= 2 and parts[-2] == 'pull' and parts[-1].isdigit() else None


def merge_step(project, http, read, base_ref, forge, merger):
    """Merge the done tasks whose pull request passes `autogates.merge_gate`. -> list of {"task", "verdict", "outcome",
    "reasons"} for the tasks it acted on; `outcome` is "merged", "refused", "failed" or "error".

    Only a task that is `done`, has a PR and no `merged_at` is looked at, so a merged task is skipped. For each: read the
    PR, its files, checks and reviews from the forge, run the gate, then record through the merge route: `refused` with
    the reasons (the service drops a repeat of the same refusal), or `decided` before the forge call and `done` / `failed`
    after it. The forge merges at the evaluated head SHA only, so a head that moved is refused by the forge and recorded
    as `failed`. A forge or service error on one task is reported and the pass goes on."""
    status, state = http('GET', STATE % quote(project, safe=''))
    if status != 200 or not isinstance(state, dict):
        return [{'task': None, 'verdict': None, 'outcome': 'error', 'reasons': ['could not read the state']}]
    loaded = gates.gate_inputs(read, base_ref)
    out = []
    for task in state.get('tasks', []):
        number, pr = task.get('number'), pr_number(task.get('pr_url'))
        if task.get('status') != 'done' or task.get('merged_at') or pr is None:
            continue
        path = MERGE % (number, quote(project, safe=''))
        try:
            info = forge.pr(pr)
            verdict = gates.merge_gate(task, info, forge.checks(pr), forge.reviews(pr), loaded['policy'], merger,
                                       forge.changed_files(pr), task_autonomy(loaded, state, task))
        except autoforge.ForgeError as e:
            out.append({'task': number, 'verdict': None, 'outcome': 'error', 'reasons': [str(e)]})
            continue
        reasons = ([loaded['error']] if loaded['error'] else []) + verdict['reasons']
        base = {'sha': verdict['sha'], 'method': verdict['method'], 'evidence': verdict['evidence'], 'reasons': reasons}
        if verdict['verdict'] != 'merge':
            http('POST', path, dict(base, phase='refused'))
            out.append({'task': number, 'verdict': 'refuse', 'outcome': 'refused', 'reasons': reasons})
            continue
        code, _body = http('POST', path, dict(base, phase='decided'))
        if code != 200:
            out.append({'task': number, 'verdict': 'merge', 'outcome': 'error', 'reasons': ['the merge was not recorded']})
            continue
        try:
            result = forge.merge(pr, verdict['sha'], verdict['method'])
        except autoforge.ForgeError as e:
            http('POST', path, dict(base, phase='failed', reasons=[str(e)]))
            out.append({'task': number, 'verdict': 'merge', 'outcome': 'failed', 'reasons': [str(e)]})
            continue
        http('POST', path, dict(base, phase='done', reasons=[], merge_sha=result.get('sha')))
        out.append({'task': number, 'verdict': 'merge', 'outcome': 'merged', 'reasons': []})
    return out


ORDER = '/api/v1/supervisor/order?project=%s'


def order_step(project, http):
    """Rank the open tasks (`autogates.order`) and send only the ranks that differ from the state's. -> list of
    {"task", "rank", "reason", "error"} for the ranks it changed (empty when the order is already right)."""
    status, state = http('GET', STATE % quote(project, safe=''))
    if status != 200 or not isinstance(state, dict):
        return [{'task': None, 'rank': None, 'reason': None, 'error': 'could not read the state'}]
    current = {t.get('number'): t.get('rank') for t in state.get('tasks', [])}
    changes = [o for o in gates.order(state.get('tasks', [])) if current.get(o['number']) != o['rank']]
    if not changes:
        return []
    code, body = http('PUT', ORDER % quote(project, safe=''), {'orders': changes})
    if code != 200 or not isinstance(body, dict):
        return [{'task': None, 'rank': None, 'reason': None,
                 'error': (body.get('error') if isinstance(body, dict) else None) or 'the order was refused'}]
    return [dict(task=o['number'], rank=o['rank'], reason=o['reason'], error=None) for o in changes]


CONTROL = '/api/v1/supervisor/runners/%s/%s?project=%s'
READY_STATUSES = ('approved', 'building', 'changes_requested', 'planning', 'open')       # what take_next hands out


def base_branch(loaded, base_ref):
    """The branch whose CI the control step reads: the policy's `base`, else the base ref without `origin/`."""
    policy = loaded.get('policy') or {}
    return policy.get('base') or (base_ref[len('origin/'):] if base_ref.startswith('origin/') else base_ref)


def ci_state(checks):
    """'red' (any check failed), 'green' (at least one check, all success) or 'unknown' (none yet, or still running)."""
    if any(c.get('state') == 'failure' for c in checks or []):
        return 'red'
    return 'green' if checks and all(c.get('state') == 'success' for c in checks) else 'unknown'


def ready_for(tasks, person):
    """True when `person` has a task a watcher could claim now: unclaimed, in a claimable status, every dependency done."""
    done = {t.get('number') for t in tasks if t.get('status') == 'done'}
    return any(t.get('assignee') == person and t.get('status') in READY_STATUSES and not t.get('runner')
               and all(d in done for d in t.get('depends_on') or []) for t in tasks)


def control_step(project, http, read, base_ref, forge):
    """Pause, resume and wake the project's runners. -> list of {"runner", "action", "reason", "error"}; `action` is
    "pause", "resume", "wake" or "silent" (listed, not touched).

    CI on the base head red: pause every runner that has no pause or stop pending. Green: resume the runners the
    supervisor paused (a person's pause is never resumed). No checks yet, or some still running: leave everything.
    Not red: wake a runner that says `watching` with no control pending when its person has a task ready, so it polls
    at once instead of after its idle wait. A runner that has not beaten for the silent limit is listed and never
    touched. A failed read or call is reported and the pass goes on."""
    status, state = http('GET', STATE % quote(project, safe=''))
    if status != 200 or not isinstance(state, dict):
        return [{'runner': None, 'action': None, 'reason': None, 'error': 'could not read the state'}]
    loaded = gates.gate_inputs(read, base_ref)
    try:
        ci = ci_state(forge.base_checks(base_branch(loaded, base_ref)))
    except autoforge.ForgeError as e:
        return [{'runner': None, 'action': None, 'reason': None, 'error': 'could not read CI on the base: %s' % e}]
    out, tasks = [], state.get('tasks', [])

    def act(runner, action, reason):
        code, body = http('POST', CONTROL % (runner['id'], action, quote(project, safe='')))
        ok = code == 200 and isinstance(body, dict)
        err = None if ok else (body.get('error') if isinstance(body, dict) else None) or 'refused'
        out.append({'runner': runner.get('label'), 'action': action, 'reason': reason, 'error': err})

    for r in state.get('runners', []):
        if r.get('silent'):
            out.append({'runner': r.get('label'), 'action': 'silent', 'reason': 'no beat for a while; not touched', 'error': None})
        elif ci == 'red' and r.get('control') in (None, 'wake'):               # a pending pause or stop is left as it is
            act(r, 'pause', 'CI on the base is red')
        elif ci == 'green' and r.get('paused_by_supervisor'):
            act(r, 'resume', 'CI on the base is green again')
        elif ci != 'red' and r.get('state') == 'watching' and not r.get('control') and ready_for(tasks, r.get('person')):
            act(r, 'wake', 'a task is ready')
    return out


def deploy_step(project, http, read, base_ref, deployer, probe=None):
    """The deploy step (autodeploy.deploy_step) with a fresh state, in the project's mode: auto only when the repository
    and the central project both say auto (task_autonomy without a task)."""
    status, state = http('GET', STATE % quote(project, safe=''))
    if status != 200 or not isinstance(state, dict):
        return [{'sha': None, 'outcome': 'error', 'reasons': ['could not read the state'], 'task': None}]
    mode = task_autonomy(gates.gate_inputs(read, base_ref), state, {})
    return autodeploy.deploy_step(project, http, read, base_ref, state, mode, deployer,
                                  **({'probe': probe} if probe else {}))


def run_pass(project, http, read, base_ref, forge, merger, deployer=None, probe=None):
    """One Supervisor pass: the plan step, the merge step, the deploy step (when a `deployer` is given), the order step,
    then the control step. -> {"plans", "merges", "deploys", "orders", "controls"}, each a list."""
    plans = plan_step(project, http, read, base_ref)
    merges = merge_step(project, http, read, base_ref, forge, merger)
    deploys = deploy_step(project, http, read, base_ref, deployer, probe) if deployer is not None else []
    return {'plans': plans, 'merges': merges, 'deploys': deploys,
            'orders': order_step(project, http),
            'controls': control_step(project, http, read, base_ref, forge)}


# ---- entry point (`/auto-runner:supervisor`) ----
# Environment (config comes from the environment only; no token is ever printed):
#   AUTO_SUPERVISOR_URL           central service base URL            (required)
#   AUTO_SUPERVISOR_TOKEN         the supervisor account's token      (required)
#   AUTO_SUPERVISOR_PROJECT       project slug                        (required)
#   AUTO_SUPERVISOR_REPO          GitHub `owner/repo`                 (required)
#   AUTO_SUPERVISOR_MERGER        the GitHub login the merge runs as  (required; the gate compares it with the PR author)
#   AUTO_SUPERVISOR_GITHUB_TOKEN  GitHub token, read by autoforge     (needed to merge)
#   AUTO_SUPERVISOR_BASE          base ref the policy is read from    (default origin/main)
#   AUTO_SUPERVISOR_REPO_DIR      local clone used to read the policy (default current directory)
#   AUTO_SUPERVISOR_PROJECTS      `watch` only: several projects, `slug=clone dir` separated by commas (each project's
#                                 policy, autonomy and deploy settings are read from its own clone, and its GitHub
#                                 `owner/repo` from that clone's origin); without it `watch` covers the single project
#                                 above. AUTO_SUPERVISOR_PROJECT and AUTO_SUPERVISOR_REPO are then not needed.
EXIT_OK, EXIT_ERRORS, EXIT_CONFIG, EXIT_REFUSED = 0, 1, 2, 3
REQUIRED = ('AUTO_SUPERVISOR_URL', 'AUTO_SUPERVISOR_TOKEN', 'AUTO_SUPERVISOR_PROJECT', 'AUTO_SUPERVISOR_REPO',
            'AUTO_SUPERVISOR_MERGER')
WHOAMI = '/api/v1/supervisor/whoami'


def urllib_http(base_url, token, timeout=30):
    """An `http(method, path, body)` over urllib with the bearer token; -> (status, json or {})."""
    def http(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(base_url.rstrip('/') + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except (urllib.error.URLError, OSError):
            return 0, {}
        try:
            return status, json.loads(raw.decode('utf-8', 'replace') or '{}')
        except ValueError:
            return status, {}
    return http


def git_reader(repo_dir):
    """`read(ref, path) -> (text, blob_sha) | None` over `git show` in a local clone (the base ref, not the working tree)."""
    def read(ref, path):
        def git(*args):
            return subprocess.run(['git', '-C', str(repo_dir), *args], capture_output=True, text=True,
                                  encoding='utf-8', errors='replace', timeout=60)
        shown = git('show', ref + ':' + path)
        if shown.returncode != 0:
            return None
        return shown.stdout, git('rev-parse', ref + ':' + path).stdout.strip() or None
    return read


def summary(result):
    lines = []
    for p in result['plans']:
        if p['task'] is None:
            lines.append('plan: ' + str(p['error']))
        else:
            lines.append('plan: task %s %s -> %s' % (p['task'], p['verdict'], p['decision'] or p['error']))
    for m in result['merges']:
        what = 'task %s %s' % (m['task'], m['outcome']) if m['task'] is not None else m['outcome']
        lines.append('merge: ' + what + (': ' + '; '.join(m['reasons']) if m['reasons'] else ''))
    for d in result.get('deploys', []):
        lines.append('deploy: %s %s%s' % ((d['sha'] or '')[:10] or 'base', d['outcome'],
                                          ': ' + '; '.join(d['reasons']) if d['reasons'] else '')
                     + (' (fix task %s)' % d['task'] if d.get('task') else ''))
    for o in result.get('orders', []):
        lines.append('order: ' + (o['error'] if o['task'] is None else
                                  'task %s -> rank %s (%s)' % (o['task'], o['rank'], o['reason'])))
    for c in result.get('controls', []):
        if c['action'] is None:
            lines.append('control: ' + c['error'])
        elif c['action'] == 'silent':
            lines.append('control: runner %s is silent (%s)' % (c['runner'], c['reason']))
        else:
            lines.append('control: %s %s (%s)%s' % (c['action'], c['runner'], c['reason'],
                                                    ': ' + c['error'] if c['error'] else ''))
    return lines or ['Nothing to do: no plan waits and no pull request is ready.']


ADD = '/api/v1/supervisor/tasks?project=%s'
ADD_REQUIRED = ('AUTO_SUPERVISOR_URL', 'AUTO_SUPERVISOR_TOKEN')


def add_task(http, project, title, source, assignee, description='', plan='', items=''):
    """POST the new task -> (status, body). `source` is what the task traces to (0010, 6a); the service requires it."""
    body = {'title': title, 'from': source, 'assignee': assignee, 'description': description}
    if plan or items:
        body.update(plan=plan, items=items)
    return http('POST', ADD % quote(project, safe=''), body)


def add_main(argv, env, http=None, out=print):
    """`add`: one task. Exit 0 = added, 1 = refused by the service (its reason is printed), 2 = missing configuration or
    an unreadable file, 3 = the token is not a supervisor's or the service did not answer."""
    ap = argparse.ArgumentParser(prog='autosupervisor.py add')
    ap.add_argument('--title', required=True)
    ap.add_argument('--from', dest='source', required=True, help='what the task traces to: a review, a plan, a request')
    ap.add_argument('--for', dest='assignee', required=True, help='the email of the member whose runner takes it')
    ap.add_argument('--description-file', default='')
    ap.add_argument('--plan', default='')
    ap.add_argument('--items', default='')
    ap.add_argument('--project', default='')
    a = ap.parse_args(argv)
    project = a.project or env.get('AUTO_SUPERVISOR_PROJECT') or ''
    missing = [k for k in ADD_REQUIRED if not env.get(k)] + ([] if project else ['AUTO_SUPERVISOR_PROJECT (or --project)'])
    if missing:
        out('Supervisor: not configured. Set ' + ', '.join(missing) + '.')
        return EXIT_CONFIG
    if bool(a.plan) != bool(a.items):
        out('Supervisor: --plan and --items go together.')
        return EXIT_CONFIG
    try:
        description, plan, items = (Path(p).read_text(encoding='utf-8') if p else ''
                                    for p in (a.description_file, a.plan, a.items))
    except OSError as e:
        out(f'Supervisor: could not read a file: {e}')
        return EXIT_CONFIG
    http = http or urllib_http(env['AUTO_SUPERVISOR_URL'], env['AUTO_SUPERVISOR_TOKEN'])
    status, body = add_task(http, project, a.title, a.source, a.assignee, description, plan, items)
    task = body.get('task') if isinstance(body, dict) else None
    if status == 201 and isinstance(task, dict):
        waits = ' Its plan waits for a person to approve it.' if task.get('status') == 'plan_review' else ''
        out(f'Task {task.get("number")}: added for {a.assignee} ({task.get("status")}).{waits}')
        return EXIT_OK
    if status == 0:
        out('Supervisor: refused. The central service did not answer.')
        return EXIT_REFUSED
    out('Not done: ' + str((body if isinstance(body, dict) else {}).get('error') or f'the service answered {status}'))
    return EXIT_REFUSED if status in (401, 403) else EXIT_ERRORS


def github_repo(url):
    """`owner/repo` from a GitHub remote URL (https or ssh, with or without .git), or None."""
    url = (url or '').strip()
    for prefix in ('https://github.com/', 'http://github.com/', 'git@github.com:', 'ssh://git@github.com/'):
        if url.startswith(prefix):
            rest = url[len(prefix):].rstrip('/')
            rest = rest[:-4] if rest.endswith('.git') else rest
            parts = rest.split('/')
            return rest if len(parts) == 2 and all(parts) else None
    return None


def origin_repo(repo_dir):
    """`owner/repo` of a clone's `origin` remote, or None."""
    got = subprocess.run(['git', '-C', str(repo_dir), 'remote', 'get-url', 'origin'], capture_output=True, text=True,
                         encoding='utf-8', errors='replace', timeout=60)
    return github_repo(got.stdout) if got.returncode == 0 else None


def watch_projects(env, origin=origin_repo):
    """-> (list of {"slug", "repo_dir", "repo"}, problems). From AUTO_SUPERVISOR_PROJECTS (`slug=dir,slug=dir`), else the
    single AUTO_SUPERVISOR_PROJECT with AUTO_SUPERVISOR_REPO_DIR and AUTO_SUPERVISOR_REPO."""
    raw = (env.get('AUTO_SUPERVISOR_PROJECTS') or '').strip()
    if not raw:
        slug, repo = env.get('AUTO_SUPERVISOR_PROJECT'), env.get('AUTO_SUPERVISOR_REPO')
        missing = [k for k, v in (('AUTO_SUPERVISOR_PROJECT', slug), ('AUTO_SUPERVISOR_REPO', repo)) if not v]
        if missing:
            return [], ['set AUTO_SUPERVISOR_PROJECTS, or ' + ' and '.join(missing)]
        return [{'slug': slug, 'repo_dir': env.get('AUTO_SUPERVISOR_REPO_DIR') or '.', 'repo': repo}], []
    out, problems = [], []
    for entry in (e.strip() for e in raw.split(',') if e.strip()):
        slug, sep, repo_dir = entry.partition('=')
        slug, repo_dir = slug.strip(), repo_dir.strip()
        if not sep or not slug or not repo_dir:
            problems.append(f'AUTO_SUPERVISOR_PROJECTS: "{entry}" is not slug=clone dir')
            continue
        if not Path(repo_dir).is_dir():
            problems.append(f'{slug}: {repo_dir} is not a folder')
            continue
        repo = origin(repo_dir)
        if repo is None:
            problems.append(f'{slug}: the origin of {repo_dir} is not a GitHub repository')
            continue
        out.append({'slug': slug, 'repo_dir': repo_dir, 'repo': repo})
    return out, problems


def bad_steps(result):
    """The step results that count as errors (exit 1)."""
    return [p for p in result['plans'] if p['error']] + [m for m in result['merges'] if m['outcome'] in ('error', 'failed')] + [
        o for o in result.get('orders', []) if o['error']] + [c for c in result.get('controls', []) if c['error']] + [
        d for d in result.get('deploys', []) if d['outcome'] in ('error', 'failed')]


def check_role(http, out):
    """-> None when the token is a supervisor's, else the exit code after saying why."""
    status, who = http('GET', WHOAMI)
    role = who.get('role') if status == 200 and isinstance(who, dict) else None
    if role == 'supervisor':
        return None
    if status != 200:
        out('Supervisor: refusing to run. The central service did not answer or rejected the token.')
    else:
        out('Supervisor: refusing to run. This token belongs to a "%s" account, not a supervisor.' % role)
    return EXIT_REFUSED


STOP = Path('.auto') / 'STOP'
WATCH_EVERY = 900
WATCH_SLICE = 30


def watch_main(argv, env, http=None, make_read=None, make_forge=None, make_deployer=None, probe=None, out=print,
               sleep=None, clock=None, origin=origin_repo, max_passes=None, stop=None):
    """`watch [--every SECONDS]`: run a pass for every project (watch_projects), sleep, and again, until `.auto/STOP`
    exists in the current folder (checked between projects and every WATCH_SLICE seconds while waiting). One project's
    error never stops the loop. Exit 0 when stopped, 2 on missing settings, 3 when the token is not a supervisor's."""
    import time as _time
    ap = argparse.ArgumentParser(prog='autosupervisor.py watch')
    ap.add_argument('--every', type=int, default=WATCH_EVERY, help='seconds between rounds (default 900)')
    a = ap.parse_args(argv)
    sleep = sleep or _time.sleep
    clock = clock or _time.monotonic
    stop = Path(stop) if stop is not None else STOP
    missing = [k for k in ('AUTO_SUPERVISOR_URL', 'AUTO_SUPERVISOR_TOKEN', 'AUTO_SUPERVISOR_MERGER') if not env.get(k)]
    projects, problems = watch_projects(env, origin)
    if missing or not projects:
        out('Supervisor: not configured. ' + '; '.join((['set ' + ', '.join(missing)] if missing else []) + problems) + '.')
        return EXIT_CONFIG
    for p in problems:
        out('Supervisor: skipping ' + p)
    http = http or urllib_http(env['AUTO_SUPERVISOR_URL'], env['AUTO_SUPERVISOR_TOKEN'])
    refused = check_role(http, out)
    if refused is not None:
        return refused
    make_read = make_read or git_reader
    make_forge = make_forge or (lambda repo: autoforge.GithubForge(repo, token=env.get('AUTO_SUPERVISOR_GITHUB_TOKEN', '')))
    make_deployer = make_deployer or autodeploy.ShellDeployer
    base_ref = env.get('AUTO_SUPERVISOR_BASE') or 'origin/main'
    out('Supervisor: watching %s every %d s (create %s to stop)' % (', '.join(p['slug'] for p in projects), a.every,
                                                                   stop.as_posix()))
    passes = 0
    while True:
        for p in projects:
            if stop.exists():
                out('Supervisor: STOP file found: stopping.')
                return EXIT_OK
            try:
                result = run_pass(p['slug'], http, make_read(p['repo_dir']), base_ref, make_forge(p['repo']),
                                  env['AUTO_SUPERVISOR_MERGER'], make_deployer(p['repo_dir']), probe)
                for line in summary(result):
                    out('[%s] %s' % (p['slug'], line))
            except Exception as e:                       # one project's failure never ends the watch
                out('[%s] error: %s: %s' % (p['slug'], type(e).__name__, e))
        passes += 1
        if max_passes is not None and passes >= max_passes:
            return EXIT_OK
        end = clock() + a.every
        while clock() < end:
            if stop.exists():
                out('Supervisor: STOP file found: stopping.')
                return EXIT_OK
            sleep(min(WATCH_SLICE, max(end - clock(), 0)))


def main(argv=None, env=None, http=None, read=None, forge=None, out=print, deployer=None, probe=None):
    """Run one pass. Exit 0 = ran clean, 1 = ran but a step reported an error, 2 = missing configuration,
    3 = refused (the token is not a supervisor's, or the service did not answer). Injectable parts are for tests.
    `main(['add', ...])` adds a task instead (see `add_main`)."""
    env = os.environ if env is None else env
    argv = [] if argv is None else list(argv)
    if argv[:1] == ['add']:
        return add_main(argv[1:], env, http=http, out=out)
    if argv[:1] == ['watch']:
        return watch_main(argv[1:], env, http=http, out=out)
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        out('Supervisor: not configured. Set ' + ', '.join(missing) + '.')
        return EXIT_CONFIG
    http = http or urllib_http(env['AUTO_SUPERVISOR_URL'], env['AUTO_SUPERVISOR_TOKEN'])
    refused = check_role(http, out)
    if refused is not None:
        return refused
    forge = forge or autoforge.GithubForge(env['AUTO_SUPERVISOR_REPO'], token=env.get('AUTO_SUPERVISOR_GITHUB_TOKEN', ''))
    read = read or git_reader(env.get('AUTO_SUPERVISOR_REPO_DIR') or '.')
    deployer = deployer or autodeploy.ShellDeployer(env.get('AUTO_SUPERVISOR_REPO_DIR') or '.')
    result = run_pass(env['AUTO_SUPERVISOR_PROJECT'], http, read, env.get('AUTO_SUPERVISOR_BASE') or 'origin/main',
                      forge, env['AUTO_SUPERVISOR_MERGER'], deployer, probe)
    for line in summary(result):
        out(line)
    return EXIT_ERRORS if bad_steps(result) else EXIT_OK


if __name__ == '__main__':
    _load('autorunner_safe', 'autosafe.py').utf8_console()
    sys.exit(main(sys.argv[1:]))
