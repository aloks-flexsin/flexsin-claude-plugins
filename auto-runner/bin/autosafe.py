"""
Safety preflight for the auto-runner plugin (stdlib only). The runner calls preflight() before it creates a worktree or starts the agent,
and auto_init.py uses required_rules() to write the same rules, so the two can never disagree.

An unattended agent must not be able to publish or destroy work. The project's `.claude/settings.json` must therefore deny:
git push, git remote, git reset --hard, git clean, checking out (or switching to) the base branch, and reading `.env` files.
`"safety": {"allow_env_read": true}` drops the `.env` read rules from both lists (env_read_allowed): the project's owner has
chosen to let the agent see those values.
Each `env_files` entry must be a relative path inside the project, exist, be ignored by git and not be tracked
(env_file_problems), so a file copied into the worktree can never be committed. `env_file_values` reads the values to redact.
With `publish.push` the remote must exist, `branch_prefix` must be set, and neither the base branch nor any run branch may
match `lanes.protected`; `publish.pull_request` needs `publish.push` (publish_problems). The agent's deny rules stay: only the
runner pushes, and only its own run branch.
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
ENV_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')


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
    rules = ['Bash(git push:*)', 'Bash(git remote:*)', 'Bash(git reset --hard:*)', 'Bash(git clean:*)']
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
    for label, command in (('git push', 'git push'), ('git remote', 'git remote'), ('git reset --hard', 'git reset --hard'),
                           ('git clean', 'git clean')):
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


def has_commit(project):
    r = subprocess.run(['git', '-C', str(project), 'rev-parse', '--verify', '-q', 'HEAD'], capture_output=True, text=True)
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
    if not (env.get(name) or '').strip():
        missing.append(f'the environment variable {name} (the token never goes in .claude/auto.json)')
    if not missing:
        return []
    return ['.claude/auto.json sets "central.enabled" but the central service cannot be used without '
            + ', '.join(missing) + '. Set them, or set "central.enabled": false to keep answers local.']


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
    return {'push': s.get('push') is True, 'pull_request': s.get('pull_request') is True,
            'remote': (s.get('remote') or '').strip() or 'origin'}


def protected_branch(name, cfg):
    """True when `name` matches one of `lanes.protected` (fnmatch patterns)."""
    import fnmatch
    lanes = cfg.get('lanes') if isinstance(cfg, dict) else None
    patterns = lanes.get('protected', []) if isinstance(lanes, dict) else []
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def publish_problems(project, cfg, base):
    """Messages when `publish` is on but cannot be done safely; [] when it is off or fine."""
    s = publish_settings(cfg)
    if s['pull_request'] and not s['push']:
        return ['.claude/auto.json: "publish.pull_request" needs "publish.push": true (a pull request needs the pushed branch).']
    if not s['push']:
        return []
    problems = []
    prefix = (cfg.get('branch_prefix') or '').strip()
    if not prefix:
        problems.append('.claude/auto.json: "publish.push" needs a "branch_prefix", so the runner only ever pushes its own branches.')
    elif base and (base.startswith(prefix) or protected_branch(prefix + '20000101-0000', cfg)):
        problems.append(f'.claude/auto.json: run branches ("{prefix}...") must not be the base branch or match "lanes.protected".')
    if not re.fullmatch(r'[A-Za-z0-9._-]+', s['remote']):
        problems.append('.claude/auto.json: "publish.remote" must be a remote name such as origin.')
    elif subprocess.run(['git', '-C', str(project), 'remote', 'get-url', s['remote']], capture_output=True).returncode != 0:
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


def preflight(project, cfg, base, doctor=None):
    """-> list of problems; empty means the runner may start. `doctor(project) -> results` is injectable for tests."""
    problems = model_problems(cfg)
    problems += base_url_problems(cfg)
    problems += central_problems(cfg)
    problems += env_file_problems(project, cfg)
    problems += publish_problems(project, cfg, base)
    problems += principles_problems(project, cfg)
    problems += capability_problems(project, cfg, doctor)
    if not has_commit(project):
        problems.append('The repository has no commit yet (the worktree is made from a commit). Commit once, then run again.')
    if not cfg['test_command'] and not cfg['check_only']:
        problems.append('No test_command is configured. Set "test_command" in .claude/auto.json, or set "check_only": true '
                        f'to run without tests (every status and log then says "{NO_TESTS}").')
    settings, err = load_settings(project)
    if err:
        problems.append(err)
    else:
        missing = missing_rules(settings, base, changelog_enabled(cfg), env_read_allowed(cfg))
        if missing:
            problems.append(f'{SETTINGS_PATH.as_posix()} must deny: ' + ', '.join(missing) +
                            '. Run auto_init.py (preview, then --apply) to add the rules without touching existing ones.')
    return problems
