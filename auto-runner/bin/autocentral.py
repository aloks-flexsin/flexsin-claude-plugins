"""
What init and join share about the central service: the token variable a project uses, how to store a token, comparing
repository URLs, and asking the service which repository a project is.

- `default_token_env(slug)` is `AUTO_RUNNER_TOKEN_<SLUG>` (upper case, `-` -> `_`), so each project has its own variable.
- `repo_key(url)` reduces `https://host/owner/repo(.git)`, `ssh://[user@]host[:port]/owner/repo` and `user@host:owner/repo`
  to `host/owner/repo` (lower case, no user, port or `.git`), so the same repository compares equal in any form; None when
  the URL is not one of those. `clean_url(url)` drops a user or password from an https URL before it is shown.
- `remote_url(dir)` is `git remote get-url origin` there (None when there is no origin).
- `lookup(url, token)` asks `GET /api/v1/project` (autodeliver.HttpTransport) and `check(info, slug, origin)` names what does
  not match. The token comes only from the environment and is never printed.
- `token_steps(...)` are the lines that tell a person what to create on the site and how to store the token (PowerShell
  `Read-Host -AsSecureString`, so the token is not echoed or kept in the shell history).
- `status(project)` (`python autocentral.py status --project DIR`, `/auto-runner:central`) says whether this checkout is
  connected and what to do next: the `central` section, the token variable (never its value), the service's answer to
  `GET /api/v1/project` and the repository check, the runner's identity, events not sent yet, blockers waiting here with
  their page links, and how the page is used. Read-only: it never pulls deliveries (that would mark answers delivered),
  never sends events and writes nothing. Exit code 0 when connected, 1 otherwise.
"""
import argparse
import importlib.util
import json
import os
import time
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


autodeliver = _load('autorunner_deliver', 'autodeliver.py')

TOKEN_ENV_PREFIX = 'AUTO_RUNNER_TOKEN_'
SLUG = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$')     # as the central service's projects.SLUG_RE
SCP = re.compile(r'^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9][A-Za-z0-9.-]*):(?P<path>[^/].*)$')
HOST = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$')
PATH = re.compile(r'^[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)+$')


def default_token_env(slug):
    return TOKEN_ENV_PREFIX + re.sub(r'[^A-Za-z0-9]', '_', (slug or '').strip()).upper()


def _key(host, path):
    path = path.strip('/')
    if path.lower().endswith('.git'):
        path = path[:-4]
    if not host or not HOST.match(host) or not PATH.match(path) or '..' in path.split('/'):
        return None
    return f'{host.lower()}/{path.lower()}'


def repo_key(url):
    url = (url or '').strip()
    if not url or any(c.isspace() for c in url):
        return None
    m = SCP.match(url)
    if m and '://' not in url:
        return _key(m.group('host'), m.group('path'))
    try:
        parts = urlsplit(url)
        parts.port
    except ValueError:
        return None
    if parts.scheme not in ('https', 'http', 'ssh', 'git') or parts.query or parts.fragment:
        return None
    return _key(parts.hostname, parts.path)


def slug_from_repo(url):
    """The slug the central service gives a repository: its name, lower case, other characters as '-'
    (`.../Case-Study-VA.git` -> 'case-study-va'); None when `url` is not a repository. The service's
    projects.slug_from_repo does the same, and uses '<owner>-<repo>' when that slug is taken, so the service's answer
    (`GET /api/v1/project`) is what counts."""
    key = repo_key(url)
    if key is None:
        return None
    return re.sub(r'[^a-z0-9]+', '-', key.rsplit('/', 1)[1].lower()).strip('-')[:64].strip('-') or None


def clean_url(url):
    """The URL without a user or password in an https/http URL (a remote may carry a token: never show it)."""
    url = (url or '').strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.scheme in ('https', 'http') and '@' in parts.netloc:
        return parts._replace(netloc=parts.netloc.rsplit('@', 1)[1]).geturl()
    return url


def remote_url(project_dir):
    r = subprocess.run(['git', '-C', str(project_dir), 'remote', 'get-url', 'origin'], capture_output=True, text=True, encoding='utf-8', errors='replace')
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def lookup(central_url, token, send=None, project=None, repo=None):
    """-> the token's project: {'slug', 'name', 'repo_url', 'account'}. A project token answers for its own project; a
    personal token needs the project named (`project`, or `repo` as host/owner/repo when the slug is not known yet).
    Raises autodeliver.TransportRefused (401, 403) or TransportUnavailable (no reply)."""
    return autodeliver.HttpTransport(central_url, token, send=send, project=project).project_info(repo=repo)


def is_personal(token):
    return (token or '').startswith('arp_')


def check(info, slug, origin):
    """-> problems ([] when the token is for `slug` and `origin` is the project's repository)."""
    if info.get('slug') != slug:
        return [f'the token belongs to project "{info.get("slug")}", not "{slug}": use a token made on that project\'s page.']
    if not info.get('repo_url'):
        return [f'project "{slug}" has no repository URL on the central service yet: an owner or admin sets it on the '
                'project page, then run this again.']
    if origin is None:
        return [f'this folder has no "origin" remote, so it cannot be checked against {info["repo_url"]}.']
    if repo_key(origin) is None or repo_key(origin) != repo_key(info['repo_url']):
        return [f'this folder\'s origin ({clean_url(origin)}) is not project "{slug}"\'s repository ({info["repo_url"]}).']
    return []


def token_steps(central_url, slug, token_env, repo_url=None):
    """The lines that tell a person what to create on the site and how to keep the token in `token_env`."""
    site = central_url.rstrip('/')
    repo = f' with repository URL {clean_url(repo_url)}' if repo_url else ''
    return [
        f'1. On {site}/projects an admin creates project "{slug}"{repo} (skip if it exists).',
        f'2. On {site}/projects/{slug}/tokens a manager or owner creates a token with the ingest and pull scopes.',
        f'3. Store it in your user environment (PowerShell; the token is not echoed):',
        f"   $t = Read-Host 'Token for {slug}' -AsSecureString; [Environment]::SetEnvironmentVariable('{token_env}', "
        f"[System.Net.NetworkCredential]::new('', $t).Password, 'User')",
        f"   macOS/Linux: read -rsp 'Token for {slug}: ' {token_env} && export {token_env}  (add it to your shell profile)",
        '4. Restart Claude Code so it sees the variable. Never put the token in .claude/auto.json or a committed file.',
    ]


GUIDE = HERE.parent / 'docs' / 'central-guide.md'
FLEXSIN_URL = 'https://auto-runner-central.vercel.app'


def _routes(project):
    here = f'"{Path(project)}"'
    slug = slug_from_repo(remote_url(project) or '')
    return [
        'Connect it, either way (each previews first and writes only after you agree):',
        '  - this checkout: /auto-runner:init --central-url URL'
        + (f' (the project, "{slug}", is read from origin)' if slug else ' (needs an origin remote to read the project from)'),
        f'      (python "{HERE / "auto_init.py"}" --project {here} --central-url URL, then --apply)',
        '  - a fresh clone of a project registered there: /auto-runner:join SLUG --central-url URL',
        f'      (python "{HERE / "auto_join.py"}" SLUG --central-url URL, then --apply)',
        f'  URL is the service address (Flexsin: {FLEXSIN_URL}); for join, SLUG is the project\'s name on it. Ask the '
        'person; never guess.',
    ]


def _sync(auto):
    path = auto / 'events.jsonl'
    total = len((path.read_text(encoding='utf-8') if path.exists() else '').split('\n')[:-1])
    state = autodeliver._push_state(auto)
    sent = min(state['lines'], total)
    line = f'Events: {total} recorded here, {total - sent} not sent yet (each run sends them as it goes and at its end)'
    if state['skipped']:
        line += f'; {len(state["skipped"])} refused by the service for good'
    return line


def _blockers(auto, site, slug):
    autoblock = _load('autorunner_block', 'autoblock.py')
    found, problems = autoblock.Store(auto).all()
    open_ = autoblock.waiting(found)
    lines = [f'Blockers waiting for an answer: {len(open_)}']
    for b in open_:
        where = f' -> {site}/projects/{slug}/blockers/{b["id"]}' if site else ''
        lines.append(f'  {b["id"]} ({b.get("task") or "no item"}): {b.get("question") or "(no question recorded)"}{where}')
    lines += [f'  unreadable blocker file: {p}' for p in problems]
    return lines


def _how(site, slug):
    return [
        'Using the page:',
        f'  - your work across projects: {site}/my; this project: {site}/projects/{slug} (overview: what needs '
        f'attention, who is working now), with tabs for tasks, runs, blockers and settings ({site}/projects/{slug}/settings/members)',
        '  - owners, managers and admins answer a blocker there (pick an option and/or write an answer); developers and '
        'viewers can read it. Accounts are made by an admin; there is no sign-up.',
        '  - the runner picks the answer up at its next pull (between iterations, or when it next starts) and resumes the item.',
        '  - the runs page shows each run: who (user and Claude account), branch, state, items done, tests and pull '
        'request, built from the events the runners send.',
        f'  - the full guide: {GUIDE}',
    ]


def status(project, env=None, send=None):
    """-> (lines, ok). See the module docstring. `env` (default os.environ) and `send` (an HTTP sender) are for tests."""
    autocfg = _load('autorunner_cfg', 'autocfg.py')
    autosafe = _load('autorunner_safe', 'autosafe.py')
    project = Path(project).resolve()
    env = os.environ if env is None else env
    auto = project / '.auto'
    lines, ok = [], False
    if subprocess.run(['git', '-C', str(project), 'rev-parse', '--git-dir'], capture_output=True).returncode != 0:
        return [f'This folder ({project}) is not a git checkout. To work on a project from the central service: sign in, '
                'list your projects and clone one (they are offered in turn):',
                f'  python "{HERE / "autocentral.py"}" projects --central-url URL   (after login start / login wait '
                'with --central-url URL)',
                f'  then /auto-runner:join SLUG --central-url URL   (Flexsin: {FLEXSIN_URL})'], False
    main_checkout = autosafe.worktree_main(project)
    if main_checkout is not None:
        return [f'This folder is a git worktree of {main_checkout}. Connect and run the auto-runner from that main checkout '
                'instead: run /auto-runner:central there. (The project and its central connection are the same; only the '
                'local .auto/ state differs, and a worktree\'s goes away with it.)'], False
    try:
        cfg = autocfg.load(project)
    except Exception as e:                                 # a broken auto.json is reported, not raised
        return [f'.claude/auto.json cannot be read: {e}', 'Fix it first (init will not overwrite it).'], False
    c = cfg.get('central') if isinstance(cfg.get('central'), dict) else None
    site, slug = '', ''
    if c is None:
        lines.append('Central service: NOT CONNECTED (no "central" section in .claude/auto.json; blockers are answered on this '
                     'machine only).')
        lines += _routes(project)
    elif not autosafe.central_enabled(cfg):
        lines.append('Central service: OFF ("central.enabled" is not true in .claude/auto.json, so answers stay local). '
                     'Set it to true to use the service again.')
    else:
        site, slug = (c.get('url') or '').strip().rstrip('/'), (c.get('project') or '').strip()
        name = autosafe.token_env(cfg)
        lines.append(f'Central service: {site or "(no url)"}, project "{slug or "(none)"}", token variable {name}')
        problems = [p for p in autosafe.central_problems(cfg, env) if 'the environment variable' not in p]
        if problems or not site or not slug:
            lines += problems or ['.claude/auto.json: "central" needs "url" and "project".']
        elif not autosafe.central_token(cfg, env)[0]:
            lines.append('Token: NOT SET. Sign this computer in (you approve a short code in your browser; Claude never '
                         'sees the token):')
            lines.append(f'  python "{HERE / "autocentral.py"}" login start --project "{project}"   then   ... login wait')
            lines.append(f'  (A machine with no person to approve can use a project token in {name} instead: '
                         f'{site}/projects/{slug}/tokens.)')
        else:
            token, source = autosafe.central_token(cfg, env)
            personal = is_personal(token)
            lines.append(f'Token: {"personal" if personal else "project"} token from {source} (not shown)')
            try:
                info = lookup(site, token, send=send, project=slug if personal else None)
            except autodeliver.TransportRefused as e:
                lines.append(f'Service: REFUSED the token ({e}). '
                             + ('You need to be a developer or above in this project, and the token must not be revoked '
                                f'(see {site}/account/tokens); sign in again with login start / login wait.' if personal else
                                f'Make a new token on {site}/projects/{slug}/tokens, store it again with the line init '
                                'prints, and restart Claude Code.'))
            except autodeliver.TransportUnavailable as e:
                lines.append(f'Service: NOT REACHABLE at {site} ({e}). Check the address and your connection, then try again.')
            else:
                found = check(info, slug, remote_url(project))
                if found:
                    lines += ['Service: answers, but ' + found[0]]
                else:
                    ok = True
                    who = f'signed in as {info["account"]}' if info.get('account') else 'a project token'
                    lines.append(f'Service: CONNECTED ({who}). Project "{slug}"; this folder\'s origin is its '
                                 f'repository ({info["repo_url"]}).')
    identity = autosafe.runner_name_problems(env)
    lines.append('Runner identity: ' + (autosafe.runner_name(env) if not identity else 'MISSING'))
    account_state = autosafe.account_check(env)
    lines.append('  Claude account check: ' + {'match': 'the signed-in account matches AUTO_RUNNER_ACCOUNT_EMAIL',
                                               'mismatch': 'MISMATCH, the signed-in account is not the one AUTO_RUNNER_ACCOUNT_EMAIL names',
                                               'not_set': 'not checked (AUTO_RUNNER_ACCOUNT_EMAIL is not set)',
                                               }.get(account_state, 'unknown (could not read `claude auth status`)'))
    for problem in identity:
        var = next(v for v in (autosafe.RUNNER_USER_ENV, autosafe.RUNNER_ACCOUNT_ENV) if v in problem)
        lines.append(f"  {problem} PowerShell: [Environment]::SetEnvironmentVariable('{var}', '<value>', 'User'); "
                     f"macOS/Linux: export {var}=<value> in your shell profile.")
    lines.append(_sync(auto))
    lines += _blockers(auto, site, slug)
    if site and slug:
        lines += _how(site, slug)
    else:
        lines.append(f'How the central page is used, and how to join: {GUIDE}')
    return lines, ok and not identity


# ---------- one-command setup (decision record 0007, step 5) ----------

def my_projects(central_url, env=None, send=None):
    """-> (account email, [{'slug', 'name', 'repo_url', 'role', 'can_run'}]) with this computer's personal token."""
    autosafe = _load('autorunner_safe', 'autosafe.py')
    token, _ = autosafe.central_token({}, env)
    if not is_personal(token):
        raise LoginError('this computer is not signed in: run login start / login wait first')
    t = autodeliver.HttpTransport(central_url, token, send=send)
    try:
        data = t.request('GET', '/api/v1/projects')
    except autodeliver.TransportRefused as e:
        raise LoginError(f'the central service refused this computer\'s token ({e}): sign in again') from None
    return data.get('account'), [p for p in data.get('projects') or [] if isinstance(p, dict)]


def _git(project, *args):
    r = subprocess.run(['git', '-C', str(project), *args], capture_output=True, text=True, encoding='utf-8',
                       errors='replace', env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    return r.returncode, (r.stdout or '').strip(), (r.stderr or '').strip()


def sync(project):
    """Bring a checkout up to date with its remote, safely -> lines. Always `git fetch origin`. Moves the local base branch
    forward only when it is checked out, the working tree is clean and it has no commits of its own (a fast-forward);
    anything else is reported, never changed."""
    autocfg = _load('autorunner_cfg', 'autocfg.py')
    project = Path(project).resolve()
    code, _, err = _git(project, 'fetch', '--quiet', 'origin')
    if code != 0:
        return [f'Sync: could not fetch from origin ({(err.splitlines() or ["no output"])[-1][:200]}). Nothing changed.']
    try:
        base = autocfg.detect_base_branch(project)
    except Exception:
        return ['Sync: fetched origin; the base branch is not known ("base_branch" in .claude/auto.json), so nothing else was done.']
    _, current, _ = _git(project, 'branch', '--show-current')
    if _git(project, 'rev-parse', '--verify', '--quiet', f'origin/{base}')[0] != 0:
        return [f'Sync: fetched origin; it has no branch {base} yet.']
    behind = int(_git(project, 'rev-list', '--count', f'{base}..origin/{base}')[1] or 0)
    ahead = int(_git(project, 'rev-list', '--count', f'origin/{base}..{base}')[1] or 0)
    dirty = bool(_git(project, 'status', '--porcelain', '--untracked-files=no')[1])
    if behind == 0:
        return [f'Sync: {base} is up to date with origin/{base}.' + (f' (It has {ahead} commit(s) of its own.)' if ahead else '')]
    if current == base and not dirty and ahead == 0:
        code, _, err = _git(project, 'merge', '--ff-only', '--quiet', f'origin/{base}')
        if code == 0:
            return [f'Sync: {base} moved forward {behind} commit(s) to origin/{base}.']
        return [f'Sync: {base} could not move forward ({(err.splitlines() or ["no output"])[-1][:200]}). Nothing changed.']
    why = (f'you are on {current or "a detached HEAD"}' if current != base else
           'there are uncommitted changes' if dirty else f'{base} has {ahead} commit(s) of its own')
    return [f'Sync: origin/{base} has {behind} new commit(s); {base} was left as it is because {why}. '
            '(The runner always starts from the fetched origin anyway.)']


# ---------- sign in from Claude (decision record 0008) ----------

PENDING = 'pending-login.json'


class LoginError(RuntimeError):
    pass


def _post(url, payload, send=None):
    """POST JSON without a token -> (status, object). No reply raises autodeliver.TransportUnavailable."""
    status, raw = (send or autodeliver.urllib_send)('POST', url, {'Content-Type': 'application/json',
                                                                  'Accept': 'application/json'},
                                                    json.dumps(payload).encode('utf-8'), 15)
    try:
        data = json.loads(raw.decode('utf-8')) if raw else {}
    except (UnicodeDecodeError, ValueError):
        data = {}
    return status, data if isinstance(data, dict) else {}


def _private_write(path, text):
    """Write `text` to `path` readable by this user only (folder 700, file 600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(text)
    os.chmod(path, 0o600)


def store_token(token, env=None, platform=None, run=None):
    """Keep the personal token without printing it -> where it went. Windows: the user environment variable
    AUTO_RUNNER_TOKEN, set by PowerShell from this process's environment (never on a command line). Elsewhere: the
    credentials file (autosafe.credentials_path, mode 600)."""
    autosafe = _load('autorunner_safe', 'autosafe.py')
    platform = platform or sys.platform
    if platform.startswith('win'):
        script = ("[Environment]::SetEnvironmentVariable('%s', $env:AUTO_RUNNER_NEW_TOKEN, 'User')"
                  % autosafe.PERSONAL_TOKEN_ENV)
        child = {**os.environ, 'AUTO_RUNNER_NEW_TOKEN': token}
        code = (run or (lambda argv, e: subprocess.run(argv, env=e, capture_output=True).returncode))(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', script], child)
        if code != 0:
            raise LoginError('could not store the token in your user environment (PowerShell failed)')
        return f'your user environment variable {autosafe.PERSONAL_TOKEN_ENV} (restart Claude Code so it sees it)'
    path = autosafe.credentials_path(env)
    _private_write(path, token + '\n')
    return f'{path} (readable only by you)'


def login_start(project, central_url=None, env=None, send=None, now=None):
    """Ask the service for a sign-in code -> lines to show. The long device code is kept in a private file next to the
    credentials (never printed); `login_wait` uses it."""
    autocfg = _load('autorunner_cfg', 'autocfg.py')
    autosafe = _load('autorunner_safe', 'autosafe.py')
    env = os.environ if env is None else env
    project = Path(project).resolve()
    if not central_url:
        c = autocfg.load(project).get('central') or {}
        central_url = (c.get('url') or '').strip() if isinstance(c, dict) else ''
    central_url = (central_url or '').strip().rstrip('/')
    if not central_url:
        raise LoginError('no central service address: connect the project first (/auto-runner:central), or pass '
                         '--central-url')
    import socket
    body = {'client_name': socket.gethostname()[:100], 'repo': repo_key(remote_url(project) or '') or ''}
    if autosafe.runner_name(env):
        body['user'], body['claude_account'] = autosafe.runner_identity(env)
    status, data = _post(central_url + '/api/v1/device/start', body, send)
    if status != 200 or not all(isinstance(data.get(k), str) for k in ('device_code', 'user_code', 'verification_uri')):
        raise LoginError(f'the central service at {central_url} did not start a sign-in ({status} {data.get("error", "")})')
    expires = (now or time.time)() + int(data.get('expires_in') or 600)
    pending = autosafe.credentials_path(env).parent / PENDING
    _private_write(pending, json.dumps({'url': central_url, 'device_code': data['device_code'],
                                        'interval': int(data.get('interval') or 5), 'expires': expires}))
    return [f'Open {data["verification_uri"]} in your browser, sign in, and type this code: {data["user_code"]}',
            'Approve it only because you started it just now. The code works for 10 minutes.',
            'Then run: login wait (it finishes by itself once you approve).']


def login_wait(project, env=None, send=None, sleep=None, now=None, store=None):
    """Poll until the person approves or denies -> lines to show. Stores the token (store_token) and prints only the
    account and where it went."""
    autosafe = _load('autorunner_safe', 'autosafe.py')
    env = os.environ if env is None else env
    pending = autosafe.credentials_path(env).parent / PENDING
    try:
        state = json.loads(pending.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise LoginError('no sign-in is waiting: run login start first') from None
    clock, nap = now or time.time, sleep or time.sleep
    interval = max(1, int(state.get('interval') or 5))
    try:
        while clock() < state['expires']:
            status, data = _post(state['url'] + '/api/v1/device/token', {'device_code': state['device_code']}, send)
            if status == 200 and isinstance(data.get('token'), str) and data['token'].startswith('arp_'):
                where = (store or store_token)(data['token'], env)
                return [f'Signed in as {data.get("email", "?")}. The token is stored in {where}; it is not shown here.']
            error = data.get('error')
            if error == 'access_denied':
                raise LoginError('the sign-in was denied in the browser')
            if error == 'expired_token':
                raise LoginError('the code expired or was already used: run login start again')
            if error == 'slow_down':
                interval += 5
            elif error != 'authorization_pending':
                raise LoginError(f'the central service answered {status} {error or ""}')
            nap(interval)
        raise LoginError('nobody approved the code within 10 minutes: run login start again')
    finally:
        if pending.exists():
            pending.unlink()


def main(argv=None):
    ap = argparse.ArgumentParser(description='The central service: check the connection (read-only), or sign this '
                                             'computer in.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    st = sub.add_parser('status', help='check the connection and print the next steps')
    st.add_argument('--project', default=os.environ.get('CLAUDE_PROJECT_DIR') or '.')
    lg = sub.add_parser('login', help='sign this computer in: start shows a code to approve in the browser, wait finishes')
    lg.add_argument('step', choices=('start', 'wait'))
    lg.add_argument('--project', default=os.environ.get('CLAUDE_PROJECT_DIR') or '.')
    lg.add_argument('--central-url', default='')
    pj = sub.add_parser('projects', help='list the projects this signed-in computer can clone')
    pj.add_argument('--project', default=os.environ.get('CLAUDE_PROJECT_DIR') or '.')
    pj.add_argument('--central-url', default='')
    sy = sub.add_parser('sync', help='fetch origin and fast-forward the base branch when that is safe')
    sy.add_argument('--project', default=os.environ.get('CLAUDE_PROJECT_DIR') or '.')
    a = ap.parse_args(argv)
    if a.cmd == 'status':
        lines, ok = status(a.project)
        print('\n'.join(lines))
        return 0 if ok else 1
    if a.cmd == 'sync':
        print('\n'.join(sync(a.project)))
        return 0
    if a.cmd == 'projects':
        url = a.central_url.strip().rstrip('/')
        if not url:
            c = _load('autorunner_cfg', 'autocfg.py').load(Path(a.project)).get('central') or {}
            url = (c.get('url') or '').strip().rstrip('/') if isinstance(c, dict) else ''
        if not url:
            print('Not done: pass --central-url (the service address)')
            return 1
        try:
            account, found = my_projects(url)
        except (LoginError, autodeliver.TransportUnavailable) as e:
            print(f'Not done: {e}')
            return 1
        print(f'Signed in as {account}. Your projects:')
        for p in found:
            print(f'  {p["slug"]}: {p["name"]} ({p["role"]}{"" if p.get("can_run") else ", cannot run runners"}) '
                  f'{p.get("repo_url") or "(no repository URL yet)"}')
        if not found:
            print('  none yet: an owner or admin invites you to a project on the central page.')
        return 0
    try:
        lines = login_start(a.project, a.central_url) if a.step == 'start' else login_wait(a.project)
    except (LoginError, autodeliver.TransportUnavailable) as e:
        print(f'Not signed in: {e}')
        return 1
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())
