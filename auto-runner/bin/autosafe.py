"""
Safety preflight for the auto-runner plugin (stdlib only). The runner calls preflight() before it creates a worktree or starts the agent,
and auto_init.py uses required_rules() to write the same rules, so the two can never disagree.

An unattended agent must not be able to publish or destroy work. The runner itself starts every agent with
`--disallowedTools` PUSH_DENIED, so no agent can `git push` whatever the project's settings say. The project's
`.claude/settings.json` must also deny: git remote, git reset --hard, git clean, checking out (or switching to) the base
branch, and reading `.env` files. (A `git push` rule there is no longer required; one that exists is left alone.)
`"safety": {"allow_env_read": true}` drops the `.env` read rules from both lists (env_read_allowed): the project's owner has
chosen to let the agent see those values.
Each `env_files` entry must be a relative path inside the project, exist, be ignored by git and not be tracked
(env_file_problems), so a file copied into the worktree can never be committed. `env_file_values` reads the values to redact.
Every run needs who runs it: AUTO_RUNNER_USER (the real person, e.g. alok) and AUTO_RUNNER_ACCOUNT (the Claude account in use,
e.g. bhawish), each lower-case letters, digits, `.`, `_`, `-`, set per person or machine and never in the committed config
(runner_name_problems). Run branches follow the team convention `<user>/<account>-claude-<surface>/<type>/<brief>` with the
surface `auto-runner`: `<user>/<account>-claude-auto-runner/<type>/<brief>` (runner_name, runner_prefix).
Publishing (push, then a pull request) is on unless `publish.push` / `publish.pull_request` is `false`. With `publish.push`
written as `true` the remote must exist; left at its default, a missing remote only turns publishing off. Neither the base
branch nor a run branch may match `lanes.protected`, and the base must not start with `<name>/`; `publish.pull_request`
needs `publish.push` (publish_problems). Only the runner pushes, and only its own run branch.
A rule counts if it is written for Bash or PowerShell; other rules in the file are never inspected or changed.
With `changelog.enabled` it must also deny `git tag` (TAG_RULES, both `git tag:*` and bare `git tag`): `autochange.py release`
prepares a release, a person tags it. Projects with the section off are not asked for these rules.
The plugin cannot enforce any of this itself: it only refuses to start while the rules are missing.

Every value in the config's `models` section must be one of MODEL_ALIASES or a full model name starting with `claude-`;
anything else (a typo such as `sonet`, an empty string, another vendor's name) is refused naming `models.<role>`, so a run
never starts on a model nobody chose. `inherit` means "no --model flag": the CLI's own default is used.

When `acceptance.browser.enabled` is true, the capability doctor (`autosetup.py`) must report every capability browser
acceptance needs (BROWSER_NEEDS) as present; otherwise the run is refused with a message ending in SETUP_HINT. The doctor is
loaded only in that case, so projects without browser acceptance are unaffected. In the same case a set
`acceptance.browser.base_url` must pass `autobrowser.check_base_url` (local or test hosts only), else the run is refused naming it.

When `central.enabled` is true, `central.url` and `central.project` must be set and the token must be in the environment variable
named by `central.token_env` (default CENTRAL_TOKEN_ENV; never the token itself in the config file), else the run is refused naming
what is missing. A `token_env` that is not a variable name, or that holds a token, is refused. The token's value is never printed.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SETTINGS_PATH = Path('.claude') / 'settings.json'
NO_TESTS = 'unverified: no tests'
SETUP_HINT = 'run /auto-runner:setup'
BROWSER_NEEDS = ('browser', 'playwright', 'mail_sink', 'accounts_ignore')
MODEL_ALIASES = ('opus', 'sonnet', 'haiku', 'inherit')
FULL_MODEL = re.compile(r'claude-[A-Za-z0-9._\[\]-]+')
TAG_RULES = ['Bash(git tag:*)', 'Bash(git tag)']
CENTRAL_TOKEN_ENV = 'AUTO_RUNNER_CENTRAL_TOKEN'
CENTRAL_TOKEN_PREFIX = 'art_'
PERSONAL_TOKEN_PREFIX = 'arp_'
PERSONAL_TOKEN_ENV = 'AUTO_RUNNER_TOKEN'
CREDENTIALS_ENV = 'AUTO_RUNNER_CREDENTIALS'          # another credentials file (tests); default credentials_path()
ENV_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')
RUNNER_USER_ENV = 'AUTO_RUNNER_USER'
RUNNER_ACCOUNT_ENV = 'AUTO_RUNNER_ACCOUNT'
RUNNER_ACCOUNT_EMAIL_ENV = 'AUTO_RUNNER_ACCOUNT_EMAIL'
RUNNER_NAME = re.compile(r'[a-z0-9][a-z0-9._-]{0,63}')
RUNNER_SURFACE = 'claude-auto-runner'
PUSH_DENIED = ('Bash(git push:*)', 'PowerShell(git push:*)')     # passed as --disallowedTools to every agent the runner starts


def utf8_console(streams=None):
    """Make the console streams (default stdout and stderr) write UTF-8, replacing what cannot be encoded, so printing
    a task title or plan with '→' never crashes on a cp1252 console. A stream without `reconfigure` is left alone;
    never raises."""
    for stream in (streams if streams is not None else (sys.stdout, sys.stderr)):
        try:
            reconfigure = getattr(stream, 'reconfigure', None)
            if reconfigure:
                reconfigure(encoding='utf-8', errors='replace')
        except Exception:                                   # a console we cannot change is still usable
            pass


def runner_prefix(name):
    """'alok/bhawish-claude-auto-runner' -> 'alok/bhawish-claude-auto-runner/': every run branch starts with it."""
    return f'{name}/'


def _plain(value):
    return bool(RUNNER_NAME.fullmatch(value)) and '..' not in value and not value.endswith(('.', '.lock'))


def runner_identity(env=None):
    """-> (user, account) from AUTO_RUNNER_USER and AUTO_RUNNER_ACCOUNT, stripped; '' for an unset one."""
    env = os.environ if env is None else env
    return (env.get(RUNNER_USER_ENV) or '').strip(), (env.get(RUNNER_ACCOUNT_ENV) or '').strip()


def runner_name(env=None):
    """-> '<user>/<account>-claude-auto-runner', the part every run branch starts with; '' unless both are set and valid."""
    user, account = runner_identity(env)
    return f'{user}/{account}-{RUNNER_SURFACE}' if user and account and _plain(user) and _plain(account) else ''


def runner_name_problems(env=None):
    """Messages when AUTO_RUNNER_USER or AUTO_RUNNER_ACCOUNT is missing or not a plain lower-case name."""
    problems = []
    for var, value, example, what in zip((RUNNER_USER_ENV, RUNNER_ACCOUNT_ENV), runner_identity(env), ('alok', 'bhawish'),
                                         ('your username (the person working)', 'the Claude account you run it with')):
        if not value:
            problems.append(f'Set {var} to {what}, for example {example}: run branches are '
                            f'<user>/<account>-{RUNNER_SURFACE}/<type>/<brief>, so the runner needs to know whose it is. '
                            'Set it per person or machine, not in auto.json.')
        elif not _plain(value):
            problems.append(f'{var} ("{value}") must be lower-case letters, digits, ".", "_" or "-", starting with a letter '
                            f'or digit (for example {example}).')
    return problems


def claude_signed_in(run=subprocess.run):
    """-> the email of the account the claude CLI is signed in with, or None (not signed in, no CLI, timeout, bad output)."""
    try:
        done = run(['claude', 'auth', 'status', '--json'], capture_output=True, text=True, timeout=20)
        if done.returncode != 0:
            return None
        data = json.loads(done.stdout)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get('loggedIn') is not True:
        return None
    email = data.get('email')
    return email.strip() if isinstance(email, str) and email.strip() else None


def account_check(env=None, run=subprocess.run):
    """-> 'match' | 'mismatch' | 'not_set' | 'unknown': the CLI's signed-in email against AUTO_RUNNER_ACCOUNT_EMAIL.
    The emails are compared here and never returned, printed or sent."""
    env = os.environ if env is None else env
    expected = (env.get(RUNNER_ACCOUNT_EMAIL_ENV) or '').strip()
    if not expected:
        return 'not_set'
    actual = claude_signed_in(run)
    if actual is None:
        return 'unknown'
    return 'match' if actual.lower() == expected.lower() else 'mismatch'


def account_check_line(check):
    """The one-line message for an account check, '' when there is nothing to say; never contains an email."""
    if check == 'mismatch':
        return (f'WARNING: the signed-in Claude account is not the one {RUNNER_ACCOUNT_EMAIL_ENV} names. The run continues; '
                'check which account it is using.')
    return ''


def changelog_enabled(cfg):
    ch = cfg.get('changelog') if isinstance(cfg, dict) else None
    return isinstance(ch, dict) and ch.get('enabled') is True


def env_read_allowed(cfg):
    s = cfg.get('safety') if isinstance(cfg, dict) else None
    return isinstance(s, dict) and s.get('allow_env_read') is True


def required_rules(base, changelog=False, env_read=False):
    """Ordered deny rules init writes and preflight demands; `base` is the branch the worktree starts from.
    `changelog` (the section is on) adds the git tag rules: the agent prepares a release, only a person tags it.
    `env_read` (safety.allow_env_read) leaves out the `.env` read rules."""
    rules = ['Bash(git remote:*)', 'Bash(git reset --hard:*)', 'Bash(git clean:*)']
    if base:
        rules += [f'Bash(git checkout {base}:*)', f'Bash(git switch {base}:*)']
    if not env_read:
        rules += ['Read(./.env)', 'Read(./.env.*)']
    if changelog:
        rules += TAG_RULES
    return rules


def load_settings(project):
    """-> (dict, error). A missing file is ({}, None); an unreadable or non-object file is ({}, message)."""
    path = Path(project) / SETTINGS_PATH
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        return {}, f'{SETTINGS_PATH.as_posix()} is not valid JSON: {e}'
    if not isinstance(data, dict):
        return {}, f'{SETTINGS_PATH.as_posix()} must hold a JSON object'
    return data, None


def deny_list(settings):
    perms = settings.get('permissions') if isinstance(settings, dict) else None
    deny = perms.get('deny') if isinstance(perms, dict) else None
    return [d for d in deny if isinstance(d, str)] if isinstance(deny, list) else []


def _command(rule):
    """'Bash(git push:*)' -> 'git push'; 'Read(./.env)' -> None (not a command rule)."""
    for tool in ('Bash(', 'PowerShell('):
        if rule.startswith(tool) and rule.endswith(')'):
            body = rule[len(tool):-1]
            return body[:-2] if body.endswith(':*') else body
    return None


def _denies(deny, command, exact=False):
    for rule in deny:
        cmd = _command(rule)
        if cmd is not None and (cmd == command or (not exact and cmd.startswith(command + ' '))):
            return True
    return False


def _denies_env(deny):
    for r in deny:
        if r.startswith('Read(') and r.endswith(')') and r[5:-1].endswith(('.env', '.env*', '.env.*')):
            return True
    return False


def missing_rules(settings, base, changelog=False, env_read=False):
    """Human labels of the deny rules that are not in `settings`."""
    deny = deny_list(settings)
    out = []
    for label, command in (('git remote', 'git remote'), ('git reset --hard', 'git reset --hard'), ('git clean', 'git clean')):
        if not _denies(deny, command):
            out.append(label)
    if base:
        if not (_denies(deny, f'git checkout {base}') or _denies(deny, 'git checkout', exact=True)):
            out.append(f'git checkout {base}')
        if not (_denies(deny, f'git switch {base}') or _denies(deny, 'git switch', exact=True)):
            out.append(f'git switch {base}')
    if not env_read and not _denies_env(deny):
        out.append('reading .env files')
    if changelog and not (_denies(deny, 'git tag') and _denies(deny, 'git tag', exact=True)):
        out.append('git tag')
    return out


def worktree_main(project):
    """-> the main checkout's folder when `project` is a linked git worktree (for example one Claude Code made under
    `.claude/worktrees/`), else None. A project is connected and run from its main checkout only: a worktree's `.auto/`
    state is its own and goes away with it."""
    r = subprocess.run(['git', '-C', str(project), 'rev-parse', '--path-format=absolute', '--git-dir', '--git-common-dir'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    lines = r.stdout.split('\n') if r.returncode == 0 else []
    if len(lines) < 2 or not lines[0].strip() or not lines[1].strip():
        return None
    git_dir, common = Path(lines[0].strip()).resolve(), Path(lines[1].strip()).resolve()
    if git_dir == common:
        return None
    return common.parent if common.name == '.git' else common


def worktree_problems(project):
    main = worktree_main(project)
    if main is None:
        return []
    return [f'This folder is a git worktree of {main}. Set the project up and run the auto-runner from that main checkout; '
            'a worktree\'s .auto/ state is its own and goes away with it.']


def has_commit(project):
    r = subprocess.run(['git', '-C', str(project), 'rev-parse', '--verify', '-q', 'HEAD'], capture_output=True, text=True, encoding='utf-8', errors='replace')
    return r.returncode == 0


def model_ok(value):
    return isinstance(value, str) and (value in MODEL_ALIASES or FULL_MODEL.fullmatch(value) is not None)


def model_problems(cfg):
    """One message per `models.<role>` whose value is not an allowed alias or a `claude-` name."""
    return [f'.claude/auto.json: "models.{role}" is not a known model ({value!r}); use one of '
            f'{", ".join(MODEL_ALIASES)} or a full name starting with claude-'
            for role, value in (cfg.get('models') or {}).items() if not model_ok(value)]


def browser_enabled(cfg):
    acc = cfg.get('acceptance') if isinstance(cfg, dict) else None
    browser = acc.get('browser') if isinstance(acc, dict) else None
    return isinstance(browser, dict) and browser.get('enabled') is True


def _sibling(name, file):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / file)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def _doctor(project):
    mod = _sibling('autorunner_setup', 'autosetup.py')
    return mod.doctor(mod.SystemProbe(project))


def base_url_problems(cfg):
    """One message when browser acceptance is on and its base_url is set but not a local or test host; [] otherwise."""
    if not browser_enabled(cfg):
        return []
    url = cfg['acceptance']['browser'].get('base_url')
    if url is None or url == '':
        return []
    allowed, reason = _sibling('autorunner_browser', 'autobrowser.py').check_base_url(url)
    return [] if allowed else [f'.claude/auto.json: "acceptance.browser.base_url" is refused: {reason}']


def central_enabled(cfg):
    c = cfg.get('central') if isinstance(cfg, dict) else None
    return isinstance(c, dict) and c.get('enabled') is True


def token_env(cfg):
    """The name of the environment variable that holds this project's central token: `central.token_env`, else
    CENTRAL_TOKEN_ENV. Each project can name its own, so several projects can each have their token set at once."""
    c = cfg.get('central') if isinstance(cfg, dict) else None
    name = (c.get('token_env') or '').strip() if isinstance(c, dict) else ''
    return name or CENTRAL_TOKEN_ENV


def credentials_path(env=None):
    """Where the personal token is kept on macOS and Linux (decision record 0008): ~/.config/auto-runner/credentials,
    mode 600, one line. The one exception to "config comes from environment variables only"."""
    env = os.environ if env is None else env
    override = (env.get(CREDENTIALS_ENV) or '').strip()
    return Path(override) if override else Path.home() / '.config' / 'auto-runner' / 'credentials'


def read_credentials(env=None):
    try:
        return credentials_path(env).read_text(encoding='utf-8').strip().splitlines()[0].strip()
    except (OSError, IndexError, UnicodeDecodeError):
        return ''


def central_token(cfg, env=None):
    """-> (token, where it came from), ('', '') when there is none. In order: a personal token in AUTO_RUNNER_TOKEN, the
    personal token in the credentials file, then the project token in `central.token_env`. Never printed."""
    env = os.environ if env is None else env
    personal = (env.get(PERSONAL_TOKEN_ENV) or '').strip()
    if personal:
        return personal, PERSONAL_TOKEN_ENV
    stored = read_credentials(env)
    if stored:
        return stored, str(credentials_path(env))
    name = token_env(cfg)
    project = (env.get(name) or '').strip()
    return (project, name) if project else ('', '')


def central_problems(cfg, env=None):
    """One message naming what is missing when `central.enabled` is true; [] otherwise. `env` defaults to os.environ."""
    if not central_enabled(cfg):
        return []
    env = os.environ if env is None else env
    name = token_env(cfg)
    if name.startswith(CENTRAL_TOKEN_PREFIX):
        return ['.claude/auto.json: "central.token_env" holds a token. Put the token in an environment variable and only that '
                'variable\'s name in "central.token_env"; replace (rotate) the token, since it was written to a file.']
    if not ENV_NAME.match(name):
        return ['.claude/auto.json: "central.token_env" must be an environment variable name (letters, digits and _).']
    missing = [f'"central.{k}"' for k in ('url', 'project') if not (cfg['central'].get(k) or '').strip()]
    if not central_token(cfg, env)[0]:
        missing.append(f'a token: sign this computer in with /auto-runner:central (a personal token), or put a project '
                       f'token in the environment variable {name} (a token never goes in .claude/auto.json)')
    if not missing:
        return []
    return ['.claude/auto.json sets "central.enabled" but the central service cannot be used without '
            + ', '.join(missing) + '. Set them, or set "central.enabled": false to keep answers local.']


SANDBOX_FIX = ('The Claude Code sandbox works on macOS, and on Linux or WSL2 with bubblewrap and socat installed '
               '(`sudo apt-get install bubblewrap socat`); on Windows run Claude Code inside a WSL2 distribution')


def sandbox_available(platform=None, which=None):
    """-> (True, '') when Claude Code's sandbox can run here, else (False, why). macOS always; Linux (WSL2 included) when
    `bwrap` and `socat` are on PATH; native Windows never (Claude Code then runs commands unsandboxed)."""
    platform = sys.platform if platform is None else platform
    which = shutil.which if which is None else which
    if platform == 'darwin':
        return True, ''
    if platform.startswith('linux'):
        missing = [t for t in ('bwrap', 'socat') if not which(t)]
        return (False, 'missing ' + ' and '.join(missing)) if missing else (True, '')
    return False, f'not supported on {platform}'


def sandbox_problems(cfg, available=None):
    """A refusal only when `sandbox.required` is true and the sandbox cannot run here; otherwise the runner keeps the
    shell allowlist and says so."""
    s = cfg.get('sandbox') if isinstance(cfg, dict) else None
    s = s if isinstance(s, dict) else {}
    if s.get('enabled') is False or s.get('required') is not True:
        return []
    ok, why = (available or sandbox_available)()
    return [] if ok else [f'sandbox.required is true but the sandbox is not available ({why}). {SANDBOX_FIX}, or set '
                          '"sandbox": {"required": false} to run with the shell allowlist instead.']


def capability_problems(project, cfg, doctor=None):
    """One message when browser acceptance is on and a capability it needs is missing; [] otherwise."""
    if not browser_enabled(cfg):
        return []
    results = (doctor or _doctor)(project)
    missing = [r['id'] for r in results if r['id'] in BROWSER_NEEDS and r['status'] == 'missing']
    if not missing:
        return []
    return ['acceptance.browser.enabled is true but the capability doctor reports missing: ' + ', '.join(missing) +
            f'; {SETUP_HINT}']


MIN_REDACT = 8      # env values shorter than this (ports, flags, `dev`) are not secrets and would garble every log


def env_file_values(paths):
    """The values in dotenv files (`KEY=value`, optional `export `, quotes stripped, `#` lines skipped) that are at least
    MIN_REDACT characters long: what the runner redacts. A file that cannot be read gives nothing."""
    found = {}
    for path in paths:
        try:
            lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            if key.startswith('export '):
                key = key[len('export '):].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                value = value[1:-1]
            if len(value) >= MIN_REDACT:
                found[f'{Path(path).name}:{key}'] = value
    return found


def path_prepend_problems(project, cfg):
    """One message per `path_prepend` entry that is not a relative folder inside the project, or does not exist (it must be
    committed: the runner puts the worktree's copy first on PATH)."""
    problems = []
    project = Path(project).resolve()
    for rel in cfg.get('path_prepend') or []:
        p = Path(rel)
        if not rel.strip() or p.is_absolute() or rel.strip()[0] in '/\\' or '..' in p.parts:
            problems.append(f'.claude/auto.json: "path_prepend" entry "{rel}" must be a folder inside the project, relative to it')
        elif not (project / p).is_dir():
            problems.append(f'.claude/auto.json: "path_prepend" entry "{rel}" is not a folder in the project')
    return problems


def env_file_problems(project, cfg):
    """One message per `env_files` entry that is not a relative path inside the project, is missing, is not ignored by git,
    or is tracked: such a file could end up in a commit."""
    problems = []
    project = Path(project).resolve()
    for rel in cfg.get('env_files') or []:
        p = Path(rel)
        if not rel.strip() or p.is_absolute() or p.drive or p.root or '..' in p.parts:
            problems.append(f'.claude/auto.json: "env_files" entry "{rel}" must be a path inside the project.')
            continue
        if not (project / p).is_file():
            problems.append(f'.claude/auto.json: "env_files" entry "{rel}" does not exist.')
            continue
        posix = p.as_posix()
        tracked = subprocess.run(['git', '-C', str(project), 'ls-files', '--error-unmatch', '--', posix],
                                 capture_output=True).returncode == 0
        ignored = subprocess.run(['git', '-C', str(project), 'check-ignore', '-q', '--no-index', '--', posix],
                                 capture_output=True).returncode == 0
        if tracked or not ignored:
            problems.append(f'.claude/auto.json: "env_files" entry "{rel}" must be ignored by git and not committed '
                            f'(add it to .gitignore{", then git rm --cached it" if tracked else ""}).')
    return problems


def publish_settings(cfg):
    """{'push', 'pull_request', 'remote'} with defaults (off, off, origin)."""
    s = cfg.get('publish') if isinstance(cfg, dict) else None
    s = s if isinstance(s, dict) else {}
    return {'push': s.get('push') is not False, 'pull_request': s.get('pull_request') is not False,
            'remote': (s.get('remote') or '').strip() or 'origin', 'explicit_push': s.get('push') is True}


def has_remote(project, remote):
    return subprocess.run(['git', '-C', str(project), 'remote', 'get-url', remote], capture_output=True).returncode == 0


def protected_branch(name, cfg):
    """True when `name` matches one of `lanes.protected` (fnmatch patterns)."""
    import fnmatch
    lanes = cfg.get('lanes') if isinstance(cfg, dict) else None
    patterns = lanes.get('protected', []) if isinstance(lanes, dict) else []
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def publish_problems(project, cfg, base, env=None):
    """Messages when `publish` is on but cannot be done safely; [] when it is off or fine. A default (unwritten) `push` with
    no remote is not a problem: the runner then simply does not publish."""
    s = publish_settings(cfg)
    if s['pull_request'] and not s['push']:
        if (cfg.get('publish') or {}).get('pull_request') is True:
            return ['.claude/auto.json: "publish.pull_request" needs "publish.push" (a pull request needs the pushed branch).']
        return []
    if not s['push']:
        return []
    problems = []
    name = runner_name(env)
    if name and base and (base.startswith(name + '/') or protected_branch(runner_prefix(name) + 'feat/x', cfg)):
        problems.append(f'Run branches ("{runner_prefix(name)}...") must not be the base branch or match "lanes.protected".')
    if not re.fullmatch(r'[A-Za-z0-9._-]+', s['remote']):
        problems.append('.claude/auto.json: "publish.remote" must be a remote name such as origin.')
    elif s['explicit_push'] and not has_remote(project, s['remote']):
        problems.append(f'"publish.push" is on but this repository has no remote "{s["remote"]}".')
    return problems


def principles_problems(project, cfg):
    """One message when `principles.file` is set but is not a readable file inside the project."""
    s = cfg.get('principles') if isinstance(cfg, dict) else None
    rel = (s.get('file') or '').strip() if isinstance(s, dict) and s.get('enabled', True) else ''
    if not rel:
        return []
    p = Path(rel)
    if p.is_absolute() or p.drive or p.root or '..' in p.parts or not (Path(project) / p).is_file():
        return [f'.claude/auto.json: "principles.file" ("{rel}") must be a file inside the project.']
    return []


def journeys_enabled(cfg):
    acc = cfg.get('acceptance') if isinstance(cfg, dict) else None
    j = acc.get('journeys') if isinstance(acc, dict) else None
    return isinstance(j, dict) and j.get('enabled') is True


def journey_problems(cfg):
    """When the journey check is on: `acceptance.journeys.start_command` and a local or test `acceptance.browser.base_url`."""
    if not journeys_enabled(cfg):
        return []
    acc = cfg['acceptance']
    browser = acc.get('browser') if isinstance(acc.get('browser'), dict) else {}
    problems = []
    if not (acc['journeys'].get('start_command') or '').strip():
        problems.append('.claude/auto.json: "acceptance.journeys" is on but "acceptance.journeys.start_command" is not set '
                        '(the command that starts the app the journeys click through)')
    allowed, reason = _sibling('autorunner_browser', 'autobrowser.py').check_base_url(browser.get('base_url'))
    if not allowed:
        problems.append(f'.claude/auto.json: "acceptance.journeys" is on but "acceptance.browser.base_url" is refused: {reason}')
    return problems


def preflight_groups(project, cfg, base, doctor=None, env=None):
    """-> [(group, message)] for every problem, in check order; empty means the runner may start. The group is a fixed
    code per check (worktree, runner_name, model, base_url, journey, central, env_file, path_prepend, publish, principles,
    capability, sandbox, no_commit, no_tests, settings), safe to send as a friction subject. Same arguments as `preflight`."""
    problems = []

    def add(group, messages):
        problems.extend((group, m) for m in messages)

    add('worktree', worktree_problems(project))
    add('runner_name', runner_name_problems(env))
    add('model', model_problems(cfg))
    add('base_url', base_url_problems(cfg))
    add('journey', journey_problems(cfg))
    add('central', central_problems(cfg))
    add('env_file', env_file_problems(project, cfg))
    add('path_prepend', path_prepend_problems(project, cfg))
    add('publish', publish_problems(project, cfg, base, env))
    add('principles', principles_problems(project, cfg))
    add('capability', capability_problems(project, cfg, doctor))
    add('sandbox', sandbox_problems(cfg))
    if not has_commit(project):
        add('no_commit', ['The repository has no commit yet (the worktree is made from a commit). Commit once, then run again.'])
    if not cfg['test_command'] and not cfg['check_only']:
        add('no_tests', ['No test_command is configured. Set "test_command" in .claude/auto.json, or set "check_only": true '
                         f'to run without tests (every status and log then says "{NO_TESTS}").'])
    settings, err = load_settings(project)
    if err:
        add('settings', [err])
    else:
        missing = missing_rules(settings, base, changelog_enabled(cfg), env_read_allowed(cfg))
        if missing:
            add('settings', [f'{SETTINGS_PATH.as_posix()} must deny: ' + ', '.join(missing) +
                             '. Run auto_init.py (preview, then --apply) to add the rules without touching existing ones.'])
    return problems


def preflight(project, cfg, base, doctor=None, env=None):
    """-> list of problems; empty means the runner may start. `doctor(project) -> results` and `env` (default os.environ) are
    injectable for tests."""
    return [message for _, message in preflight_groups(project, cfg, base, doctor, env)]
