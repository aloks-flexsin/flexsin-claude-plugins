"""The Supervisor's pass (decision 0010). Today: the plan step and the merge step (`run_pass` does both). Standard library only; all I/O is injected.

`plan_step(project, http, read, base_ref)` reads the project's state (`GET /api/v1/supervisor/state`), loads the policy from
the base ref through `read(ref, path) -> (text, blob_sha) | None` (autogates.load_policy), runs `plan_gate` on every task
that awaits plan review and has no supervisor decision on its current plan post (`plan_decided` is null), and posts the
verdict to `POST /api/v1/supervisor/tasks/<n>/plan-decision`: approve -> `approve`, changes -> `changes`, person ->
`escalate` (the plan stays for a person). The service sends a third `changes` back as `escalate` and ignores a repeated
decision for a plan it already decided, so running a pass twice never duplicates a decision.

`http(method, path, body=None) -> (status, json)` is the only way out: tests inject a fake, a real run wraps urllib with the
supervisor's personal token (read from the environment, never logged). A failing call is reported in the result and the
pass goes on with the next task.
"""
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

STATE = '/api/v1/supervisor/state?project=%s'
DECISION = '/api/v1/supervisor/tasks/%d/plan-decision?project=%s'
VERDICT_TO_DECISION = {'approve': 'approve', 'changes': 'changes', 'person': 'escalate'}


def plan_step(project, http, read, base_ref):
    """-> list of {"task", "verdict", "decision", "status", "error"} for the tasks it acted on (none for a quiet pass)."""
    status, state = http('GET', STATE % quote(project, safe=''))
    if status != 200 or not isinstance(state, dict):
        return [{'task': None, 'verdict': None, 'decision': None, 'status': status, 'error': 'could not read the state'}]
    loaded = gates.load_policy(read, base_ref)
    out = []
    for task in state.get('tasks', []):
        if task.get('status') != 'plan_review' or task.get('plan_decided'):
            continue
        gate = gates.plan_gate(task, loaded['policy'])
        if loaded['error']:
            gate['reasons'] = [loaded['error']] + gate['reasons']
        decision = VERDICT_TO_DECISION[gate['verdict']]
        code, body = http('POST', DECISION % (task['number'], quote(project, safe='')),
                          {'decision': decision, 'reasons': gate['reasons'], 'policy_sha': loaded['sha']})
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
    loaded = gates.load_policy(read, base_ref)
    out = []
    for task in state.get('tasks', []):
        number, pr = task.get('number'), pr_number(task.get('pr_url'))
        if task.get('status') != 'done' or task.get('merged_at') or pr is None:
            continue
        path = MERGE % (number, quote(project, safe=''))
        try:
            info = forge.pr(pr)
            verdict = gates.merge_gate(task, info, forge.checks(pr), forge.reviews(pr), loaded['policy'], merger,
                                       forge.changed_files(pr))
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


def run_pass(project, http, read, base_ref, forge, merger):
    """One Supervisor pass: the plan step, then the merge step. -> {"plans": [...], "merges": [...]}."""
    return {'plans': plan_step(project, http, read, base_ref),
            'merges': merge_step(project, http, read, base_ref, forge, merger)}


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
    return lines or ['Nothing to do: no plan waits and no pull request is ready.']


def main(argv=None, env=None, http=None, read=None, forge=None, out=print):
    """Run one pass. Exit 0 = ran clean, 1 = ran but a step reported an error, 2 = missing configuration,
    3 = refused (the token is not a supervisor's, or the service did not answer). Injectable parts are for tests."""
    env = os.environ if env is None else env
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        out('Supervisor: not configured. Set ' + ', '.join(missing) + '.')
        return EXIT_CONFIG
    http = http or urllib_http(env['AUTO_SUPERVISOR_URL'], env['AUTO_SUPERVISOR_TOKEN'])
    status, who = http('GET', WHOAMI)
    role = who.get('role') if status == 200 and isinstance(who, dict) else None
    if role != 'supervisor':
        if status != 200:
            out('Supervisor: refusing to run. The central service did not answer or rejected the token.')
        else:
            out('Supervisor: refusing to run. This token belongs to a "%s" account, not a supervisor.' % role)
        return EXIT_REFUSED
    forge = forge or autoforge.GithubForge(env['AUTO_SUPERVISOR_REPO'], token=env.get('AUTO_SUPERVISOR_GITHUB_TOKEN', ''))
    read = read or git_reader(env.get('AUTO_SUPERVISOR_REPO_DIR') or '.')
    result = run_pass(env['AUTO_SUPERVISOR_PROJECT'], http, read, env.get('AUTO_SUPERVISOR_BASE') or 'origin/main',
                      forge, env['AUTO_SUPERVISOR_MERGER'])
    for line in summary(result):
        out(line)
    bad = [p for p in result['plans'] if p['error']] + [m for m in result['merges'] if m['outcome'] in ('error', 'failed')]
    return EXIT_ERRORS if bad else EXIT_OK


if __name__ == '__main__':
    _load('autorunner_safe', 'autosafe.py').utf8_console()
    sys.exit(main())
