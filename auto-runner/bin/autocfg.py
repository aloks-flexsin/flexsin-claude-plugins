"""
Config loader for the auto-runner plugin (stdlib only).

The project file is `.claude/auto.json`; every key is optional and a missing file means all defaults:

    test_command     command that proves the suite is green ('' = none; then check_only must be true to run)
    check_only       true = no tests exist; every status and log then says "unverified: no tests"
    queue_file       the one file a person reads (default .auto/queue.md)
    rules_files      files the agent must read first (none assumed; list them)
    share_dirs       folders linked into the worktree instead of copied (a venv, node_modules)
    worktree_suffix  worktree folder = <project folder name><suffix> next to the project (default -auto)
    base_branch      branch the worktree starts from ('' = detect)
    branch_prefix    run branches are <prefix><stamp> (default auto/)
    commit_trailer   line added to each commit message ('' = none)

Schema v2 adds optional sections (objects; each nested key optional, unknown nested keys and wrong types fail naming
`section.key`). They are parsed and validated only: a section is in the loaded dict only when the file sets it, so a
config with only the old keys loads exactly as before. Read them with `section(cfg, name)`.

    models   role -> model alias or name (implementer, planner, plan_reviewer, work_reviewer, tester, scribe)
    lanes    protected / sandbox branch patterns, deploy_command (nothing acts on lanes yet)
    review   model, max_plan_revisions, max_fix_rounds, max_budget_usd
    budgets  iteration_usd, run_usd, max_iterations
    docs     adr_dir, required (files a run must keep up to date), map (the doc map autodocs.py reads;
             default docs/doc-map.json, read it with `docs_map(cfg)`), check (false turns the runner's docs gate
             off; default on, and the gate runs only when the map file exists; read it with `docs_check(cfg)`)
    central  enabled, url, project (never a token: secrets do not belong in this file)
    locks    path, stale_after_minutes
    acceptance  browser: enabled, base_url, command, headless, exploratory,
                         accounts: method (seed_command | signup), seed_command
                (nested objects are checked the same way, naming the full path such as `acceptance.browser.accounts.method`;
                a set base_url must pass `autobrowser.check_base_url`, which preflight checks)
    changelog   enabled, file, versioning (semver); defaults off, CHANGELOG.md, semver (read them with `changelog(cfg)`).
                When on, queue items need a `*Kind:*` line and the prompt asks for one change sentence per item.

`plugins/auto-runner/config.schema.json` describes every key; keep it in step with DEFAULTS and SECTIONS (a test checks).

Base branch order: config, `git symbolic-ref refs/remotes/origin/HEAD`, the current branch when it is main/master/trunk/develop,
otherwise BaseBranchUnknown: one answer is needed from the person, we never guess.
"""
import json
import subprocess
from pathlib import Path

CONFIG_PATH = Path('.claude') / 'auto.json'
KNOWN_BRANCHES = ('main', 'master', 'trunk', 'develop')

DEFAULTS = {
    'test_command': '',
    'check_only': False,
    'queue_file': '.auto/queue.md',
    'rules_files': [],
    'share_dirs': [],
    'worktree_suffix': '-auto',
    'base_branch': '',
    'branch_prefix': 'auto/',
    'commit_trailer': '',
}

# Nested key -> kind: 'text', 'flag', 'count' (whole number >= 0), 'amount' (number >= 0), 'texts' (list of text).
SECTIONS = {
    'models': {r: 'text' for r in ('implementer', 'planner', 'plan_reviewer', 'work_reviewer', 'tester', 'scribe')},
    'lanes': {'protected': 'texts', 'sandbox': 'texts', 'deploy_command': 'text'},
    'review': {'model': 'text', 'max_plan_revisions': 'count', 'max_fix_rounds': 'count', 'max_budget_usd': 'amount'},
    'budgets': {'iteration_usd': 'amount', 'run_usd': 'amount', 'max_iterations': 'count'},
    'docs': {'adr_dir': 'text', 'required': 'texts', 'map': 'text', 'check': 'flag'},
    'central': {'enabled': 'flag', 'url': 'text', 'project': 'text'},
    'locks': {'path': 'text', 'stale_after_minutes': 'count'},
    # A dict kind is a nested object (same rules, deeper path); a tuple kind is one of those words.
    'acceptance': {'browser': {'enabled': 'flag', 'base_url': 'text', 'command': 'text', 'headless': 'flag',
                               'exploratory': 'flag',
                               'accounts': {'method': ('seed_command', 'signup'), 'seed_command': 'text'}}},
    'changelog': {'enabled': 'flag', 'file': 'text', 'versioning': ('semver',)},
}
# Values a section means when the file leaves a key out (section() still returns only what the file sets).
CHANGELOG_DEFAULTS = {'enabled': False, 'file': 'CHANGELOG.md', 'versioning': 'semver'}
DOCS_MAP_DEFAULT = 'docs/doc-map.json'
KIND_TEXT = {'text': 'text', 'flag': 'true/false', 'count': 'a whole number >= 0',
             'amount': 'a number >= 0', 'texts': 'a list of text'}


class ConfigError(ValueError):
    pass


class BaseBranchUnknown(RuntimeError):
    pass


def load(project_dir):
    """-> dict with every key of DEFAULTS. Unknown keys and wrong types are errors that name the key."""
    path = Path(project_dir) / CONFIG_PATH
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    if not path.exists():
        return cfg
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise ConfigError(f'{CONFIG_PATH.as_posix()} is not valid JSON: {e}')
    if not isinstance(raw, dict):
        raise ConfigError(f'{CONFIG_PATH.as_posix()} must hold a JSON object')
    for key, value in raw.items():
        if key in SECTIONS:
            cfg[key] = _section(key, value)
            continue
        if key not in DEFAULTS:
            raise ConfigError(f'{CONFIG_PATH.as_posix()}: unknown key "{key}" (known: {", ".join([*DEFAULTS, *SECTIONS])})')
        want = DEFAULTS[key]
        ok = (isinstance(value, bool) if isinstance(want, bool)
              else all(isinstance(x, str) for x in value) if isinstance(want, list) and isinstance(value, list)
              else isinstance(value, type(want)) and not isinstance(value, bool))
        if not ok:
            raise ConfigError(f'{CONFIG_PATH.as_posix()}: "{key}" must be {"true/false" if isinstance(want, bool) else "a list of text" if isinstance(want, list) else "text"}')
        cfg[key] = value
    return cfg


def _kind_ok(kind, v):
    if isinstance(kind, tuple):
        return isinstance(v, str) and v in kind
    if kind == 'text':
        return isinstance(v, str)
    if kind == 'flag':
        return isinstance(v, bool)
    if kind == 'texts':
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    if kind == 'count':
        return isinstance(v, int) and not isinstance(v, bool) and v >= 0
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0      # amount


def _section(name, value, spec=None):
    where = CONFIG_PATH.as_posix()
    if not isinstance(value, dict):
        raise ConfigError(f'{where}: "{name}" must be an object')
    spec = SECTIONS[name] if spec is None else spec
    out = {}
    for k, v in value.items():
        path = f'{name}.{k}'
        if k not in spec:
            raise ConfigError(f'{where}: unknown key "{path}" (known: {", ".join(spec)})')
        if isinstance(spec[k], dict):
            out[k] = _section(path, v, spec[k])
            continue
        if not _kind_ok(spec[k], v):
            want = ('one of ' + ', '.join(spec[k])) if isinstance(spec[k], tuple) else KIND_TEXT[spec[k]]
            raise ConfigError(f'{where}: "{path}" must be {want}')
        out[k] = list(v) if isinstance(v, list) else v
    return out


def _copy(value):
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    return list(value) if isinstance(value, list) else value


def section(cfg, name):
    """The section as set in the file, or {} when it is not set. Never a shared object."""
    if name not in SECTIONS:
        raise KeyError(name)
    return _copy(cfg.get(name, {}))


def changelog(cfg):
    """The changelog section with its defaults filled in (off, CHANGELOG.md, semver)."""
    return {**CHANGELOG_DEFAULTS, **section(cfg, 'changelog')}


def docs_map(cfg):
    """The doc map path (relative to the project) from `docs.map`, else docs/doc-map.json."""
    return section(cfg, 'docs').get('map') or DOCS_MAP_DEFAULT


def docs_check(cfg):
    """False only when `docs.check` is set to false: the runner's docs gate is on by default (it needs a map to do anything)."""
    return section(cfg, 'docs').get('check', True)


def _git(project_dir, *args):
    r = subprocess.run(['git', '-C', str(project_dir), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


def detect_base_branch(project_dir, cfg=None):
    cfg = cfg if cfg is not None else load(project_dir)
    if cfg['base_branch']:
        return cfg['base_branch']
    head = _git(project_dir, 'symbolic-ref', 'refs/remotes/origin/HEAD')
    if head.startswith('refs/remotes/origin/'):
        return head[len('refs/remotes/origin/'):]
    current = _git(project_dir, 'symbolic-ref', '--short', 'HEAD')
    if current in KNOWN_BRANCHES:
        return current
    raise BaseBranchUnknown(
        f'Cannot tell which branch to start from (current: {current or "detached"}). '
        f'Set "base_branch" in {CONFIG_PATH.as_posix()}.')
