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
"""
import importlib.util
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
    r = subprocess.run(['git', '-C', str(project_dir), 'remote', 'get-url', 'origin'], capture_output=True, text=True)
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
