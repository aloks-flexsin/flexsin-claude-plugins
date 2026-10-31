"""
Safety preflight for the auto-runner plugin (stdlib only). The runner calls preflight() before it creates a worktree or starts the agent,
and auto_init.py uses required_rules() to write the same rules, so the two can never disagree.

An unattended agent must not be able to publish or destroy work. The project's `.claude/settings.json` must therefore deny:
git push, git remote, git reset --hard, git clean, checking out (or switching to) the base branch, and reading `.env` files.
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
CENTRAL_TOKEN_ENV (never in the config file), else the run is refused naming what is missing. The token's value is never printed.
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


def changelog_enabled(cfg):
    ch = cfg.get('changelog') if isinstance(cfg, dict) else None
    return isinstance(ch, dict) and ch.get('enabled') is True


def required_rules(base, changelog=False):
    """Ordered deny rules init writes and preflight demands; `base` is the branch the worktree starts from.
    `changelog` (the section is on) adds the git tag rules: the agent prepares a release, only a person tags it."""
    rules = ['Bash(git push:*)', 'Bash(git remote:*)', 'Bash(git reset --hard:*)', 'Bash(git clean:*)']
    if base:
        rules += [f'Bash(git checkout {base}:*)', f'Bash(git switch {base}:*)']
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


def missing_rules(settings, base, changelog=False):
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
    if not _denies_env(deny):
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


def central_problems(cfg, env=None):
    """One message naming what is missing when `central.enabled` is true; [] otherwise. `env` defaults to os.environ."""
    if not central_enabled(cfg):
        return []
    env = os.environ if env is None else env
    missing = [f'"central.{k}"' for k in ('url', 'project') if not (cfg['central'].get(k) or '').strip()]
    if not (env.get(CENTRAL_TOKEN_ENV) or '').strip():
        missing.append(f'the environment variable {CENTRAL_TOKEN_ENV} (the token never goes in .claude/auto.json)')
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


def preflight(project, cfg, base, doctor=None):
    """-> list of problems; empty means the runner may start. `doctor(project) -> results` is injectable for tests."""
    problems = model_problems(cfg)
    problems += base_url_problems(cfg)
    problems += central_problems(cfg)
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
        missing = missing_rules(settings, base, changelog_enabled(cfg))
        if missing:
            problems.append(f'{SETTINGS_PATH.as_posix()} must deny: ' + ', '.join(missing) +
                            '. Run auto_init.py (preview, then --apply) to add the rules without touching existing ones.')
    return problems
