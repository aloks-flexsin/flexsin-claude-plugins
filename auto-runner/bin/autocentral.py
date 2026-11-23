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


def lookup(central_url, token, send=None):
    """-> the project the token belongs to: {'slug', 'name', 'repo_url'}. Raises autodeliver.TransportRefused (401, 403)
    or TransportUnavailable (no reply)."""
    return autodeliver.HttpTransport(central_url, token, send=send).project_info()


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
        f'  - your projects: {site}/projects; this one: {site}/projects/{slug}/blockers (blockers), '
        f'{site}/projects/{slug}/members, {site}/projects/{slug}/tokens',
        '  - owners, managers and admins answer a blocker there (pick an option and/or write an answer); developers and '
        'viewers can read it. Accounts are made by an admin; there is no sign-up.',
        '  - the runner picks the answer up at its next pull (between iterations, or when it next starts) and resumes the item.',
        '  - today the page shows blockers only: run events are stored there but no runs page exists yet.',
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
        elif not (env.get(name) or '').strip():
            lines.append(f'Token: NOT SET ({name} is empty in this environment). Steps (you do them; never paste the token '
                         'into Claude or a file):')
            lines += ['  ' + s for s in token_steps(site, slug, name, remote_url(project))]
        else:
            lines.append(f'Token: set in {name} (not shown)')
            try:
                info = lookup(site, env[name].strip(), send=send)
            except autodeliver.TransportRefused as e:
                lines.append(f'Service: REFUSED the token ({e}). Make a new token on {site}/projects/{slug}/tokens, store '
                             'it again with the line init prints, and restart Claude Code.')
            except autodeliver.TransportUnavailable as e:
                lines.append(f'Service: NOT REACHABLE at {site} ({e}). Check the address and your connection, then try again.')
            else:
                found = check(info, slug, remote_url(project))
                if found:
                    lines += ['Service: answers, but ' + found[0]]
                else:
                    ok = True
                    lines.append(f'Service: CONNECTED. The token is project "{slug}"\'s and this folder\'s origin is its '
                                 f'repository ({info["repo_url"]}).')
    identity = autosafe.runner_name_problems(env)
    lines.append('Runner identity: ' + (autosafe.runner_name(env) if not identity else 'MISSING'))
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


def main(argv=None):
    ap = argparse.ArgumentParser(description='Is this checkout connected to the central service, and what to do next '
                                             '(read-only).')
    sub = ap.add_subparsers(dest='cmd', required=True)
    st = sub.add_parser('status', help='check the connection and print the next steps')
    st.add_argument('--project', default=os.environ.get('CLAUDE_PROJECT_DIR') or '.')
    a = ap.parse_args(argv)
    lines, ok = status(a.project)
    print('\n'.join(lines))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
