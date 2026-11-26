"""The Supervisor's deploy step (decision record 0012, Phase 5): after merges, deploy the base head, check it, roll back
and add a fix task when it fails. Standard library only; every outside call is injected so tests need no network.

`deploy_step(project, http, read, base_ref, state, mode, deployer, probe)` does, in auto mode only (`mode == "auto"`):
1. read the `deploy` section of `.claude/auto.json` on the base (`read(ref, path)`, as the gates do); no `command`: nothing;
2. take the base head (`deployer.head(base_ref)`); when `state["last_deploy"]` already finished that SHA (done, failed
   or rolled back): nothing, so a pass that finds no new merge deploys nothing;
3. record `started` (`POST /api/v1/supervisor/deploys`), run the command in a detached worktree of that SHA
   (`deployer.run(command, sha, timeout)`), then check `smoke_url` (any 2xx; up to `smoke_tries` tries, 10 s apart);
4. green: record `done`. Red (non-zero exit, timeout or smoke check): add a fix task to the pool (`POST
   /api/v1/supervisor/tasks`, from "failed deploy of <sha>"), record `failed` with that task's number, run
   `rollback_command` when set and record `rolled_back` with its result.
In manual mode a person deploys: the step does nothing. Command output is cut to its last lines and every value of an
environment variable whose name holds TOKEN, SECRET, KEY or PASSWORD is replaced by `***` before it leaves the machine.

`ShellDeployer(repo_dir)` runs commands with the shell in `<repo_dir>-deploy`, a detached git worktree it creates and
moves to each SHA, so the person's own checkout is never touched. `http_probe(url)` is a GET that returns the status.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

DEPLOYS = '/api/v1/supervisor/deploys?project=%s'
ADD = '/api/v1/supervisor/tasks?project=%s'
CONFIG = '.claude/auto.json'
FINISHED = ('done', 'failed', 'rolled_back')
DEFAULTS = {'command': '', 'smoke_url': '', 'rollback_command': '', 'smoke_tries': 3, 'timeout_s': 600}
SMOKE_PAUSE_S = 10
TAIL = 1500
SECRET_WORDS = ('TOKEN', 'SECRET', 'KEY', 'PASSWORD')


def deploy_config(read, base_ref):
    """-> (the deploy section with DEFAULTS filled in, error or None), read from `.claude/auto.json` on the base.
    No file or no section: no command. An unreadable file or a wrong type is an error (and nothing is deployed)."""
    got = read(base_ref, CONFIG)
    if got is None:
        return dict(DEFAULTS), None
    try:
        data = json.loads(got[0])
    except ValueError:
        return dict(DEFAULTS), f'{CONFIG} on {base_ref} is not valid JSON: not deploying'
    section = data.get('deploy', {}) if isinstance(data, dict) else {}
    if not isinstance(section, dict):
        return dict(DEFAULTS), f'"deploy" in {CONFIG} must be an object: not deploying'
    out = dict(DEFAULTS)
    for key, value in section.items():
        if key not in DEFAULTS:
            return dict(DEFAULTS), f'unknown key "deploy.{key}" in {CONFIG}: not deploying'
        if isinstance(DEFAULTS[key], int) and not (isinstance(value, int) and not isinstance(value, bool) and value >= 0):
            return dict(DEFAULTS), f'"deploy.{key}" must be a whole number >= 0: not deploying'
        if isinstance(DEFAULTS[key], str) and not isinstance(value, str):
            return dict(DEFAULTS), f'"deploy.{key}" must be text: not deploying'
        out[key] = value
    return out, None


def redact(text, env=None):
    """`text` with every value (6+ characters) of a TOKEN/SECRET/KEY/PASSWORD environment variable replaced by ***."""
    env = os.environ if env is None else env
    for name, value in env.items():
        if value and len(value) >= 6 and any(w in name.upper() for w in SECRET_WORDS):
            text = text.replace(value, '***')
    return text


def tail(text, env=None):
    return redact((text or '')[-TAIL:], env)


class ShellDeployer:
    """Runs deploy commands in a detached worktree next to `repo_dir` (`<repo_dir>-deploy`). `runner` is subprocess.run
    (injectable for tests)."""

    def __init__(self, repo_dir, runner=subprocess.run):
        self.repo = Path(repo_dir).resolve()
        self.worktree = self.repo.parent / (self.repo.name + '-deploy')
        self.runner = runner

    def _git(self, *args, cwd=None):
        return self.runner(['git', '-C', str(cwd or self.repo), *args], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=300)

    def head(self, base_ref):
        """-> the commit SHA of `base_ref`, fetching first when it is a remote branch (origin/...)."""
        if '/' in base_ref:
            self._git('fetch', '-q', base_ref.split('/', 1)[0])
        got = self._git('rev-parse', base_ref)
        if got.returncode != 0 or not got.stdout.strip():
            raise RuntimeError(f'could not read {base_ref}: {got.stderr.strip()[-200:]}')
        return got.stdout.strip()

    def run(self, command, sha, timeout):
        """-> (exit code, output); exit code None on a timeout. The worktree is moved to `sha` first."""
        if self.worktree.exists():
            moved = self._git('checkout', '-q', '--detach', '--force', sha, cwd=self.worktree)
        else:
            moved = self._git('worktree', 'add', '-q', '--detach', '--force', str(self.worktree), sha)
        if moved.returncode != 0:
            return 1, f'could not check out {sha} in {self.worktree}: {moved.stderr.strip()[-300:]}'
        try:
            done = self.runner(command, shell=True, cwd=str(self.worktree), capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=timeout or None)
        except subprocess.TimeoutExpired:
            return None, f'timed out after {timeout} s'
        return done.returncode, (done.stdout or '') + (done.stderr or '')


def http_probe(url, timeout=20):
    """GET `url` -> its HTTP status, or 0 when nothing answered."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='GET'), timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError, ValueError):
        return 0


def smoke(url, tries, probe, sleep):
    """-> (ok, reason). No URL: ok. Otherwise up to `tries` GETs (at least one), SMOKE_PAUSE_S apart, until a 2xx."""
    if not url:
        return True, None
    last = 0
    for n in range(max(tries, 1)):
        if n:
            sleep(SMOKE_PAUSE_S)
        last = probe(url)
        if 200 <= last < 300:
            return True, None
    return False, f'smoke check of {url} answered {last or "nothing"} after {max(tries, 1)} tries'


def deploy_step(project, http, read, base_ref, state, mode, deployer, probe=http_probe, sleep=time.sleep, env=None):
    """-> [] when nothing was due, else [{"sha", "outcome", "reasons", "task"}], `outcome` one of "deployed", "failed"
    (a fix task was added) or "error" (the step could not run or record). See the module docstring."""
    if mode != 'auto':
        return []
    cfg, error = deploy_config(read, base_ref)
    if error:
        return [{'sha': None, 'outcome': 'error', 'reasons': [error], 'task': None}]
    if not cfg['command']:
        return []
    try:
        sha = deployer.head(base_ref)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        return [{'sha': None, 'outcome': 'error', 'reasons': [str(e)], 'task': None}]
    last = (state or {}).get('last_deploy') or {}
    if last.get('sha') == sha and last.get('phase') in FINISHED:
        return []
    path = DEPLOYS % quote(project, safe='')

    def record(phase, reasons, output='', task=None):
        code, _ = http('POST', path, {'phase': phase, 'sha': sha, 'reasons': reasons, 'output': tail(output, env),
                                      'task': task})
        return code == 200

    if not record('started', ['merged changes on %s' % base_ref]):
        return [{'sha': sha, 'outcome': 'error', 'reasons': ['the deploy could not be recorded'], 'task': None}]
    code, output = deployer.run(cfg['command'], sha, cfg['timeout_s'])
    if code == 0:
        ok, why = smoke(cfg['smoke_url'], cfg['smoke_tries'], probe, sleep)
    else:
        ok, why = False, ('the deploy command timed out' if code is None else f'the deploy command exited {code}')
    if ok:
        record('done', [], output)
        return [{'sha': sha, 'outcome': 'deployed', 'reasons': [], 'task': None}]
    reasons = [why]
    description = (f'The supervisor deployed {sha} from {base_ref} and it failed: {why}.\n\n'
                   f'Last lines of the deploy output:\n\n```\n{tail(output, env)}\n```\n\n'
                   'Fix the cause on a branch; the next merge is deployed again.')
    status, body = http('POST', ADD % quote(project, safe=''),
                        {'title': f'Fix the failed deploy of {sha[:10]}', 'from': f'failed deploy of {sha} (supervisor)',
                         'description': description})
    task = (body.get('task') or {}).get('number') if status == 201 and isinstance(body, dict) else None
    if task is None:
        reasons.append('the fix task could not be added')
    record('failed', reasons, output, task)
    if cfg['rollback_command']:
        rcode, rout = deployer.run(cfg['rollback_command'], sha, cfg['timeout_s'])
        record('rolled_back', ['rollback command exited %s' % ('with a timeout' if rcode is None else rcode)], rout, task)
        reasons.append('rolled back' if rcode == 0 else 'the rollback failed too')
    return [{'sha': sha, 'outcome': 'failed', 'reasons': reasons, 'task': task}]
