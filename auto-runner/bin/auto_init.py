"""
Set a project up for the auto-runner. Shows a preview; nothing is written without --apply.

    python auto_init.py --central-url https://central.example.com --central-project my-app   # preview
    python auto_init.py --central-url ... --central-project my-app --apply            # write what the preview listed
    python auto_init.py --local            # no central service: blockers are answered on this machine
    python auto_init.py --apply --with-rules   # also write a short AUTO_RULES.md when the project has no CLAUDE.md
    python auto_init.py --remove           # preview of what init added and would take away
    python auto_init.py --remove --apply   # take it away

What it does, and only this:
  * `.claude/settings.json`: appends the deny rules the preflight demands (git push/remote/reset --hard/clean, checkout of the base
    branch, reading .env; git tag too when `changelog.enabled`). Existing rules, other keys and the order are kept; duplicates are skipped; nothing is removed or overwritten.
  * `.claude/auto.json`: created only if absent, with the detected test command (pytest, npm test, dotnet test, go test, cargo test),
    `env_files: [".env"]` when a gitignored `.env` exists (copied into each run's worktree, values redacted), and with
    --allow-env-read `"safety": {"allow_env_read": true}` (then the .env read deny rules are not written).
  * The central service is the default. With --central-url and --central-project, the `central` section (enabled, url, project and
    `token_env`, default AUTO_RUNNER_TOKEN_<SLUG>) goes into a new auto.json, or into an existing one that has no `central` key.
    The preview says what to create on the site and how to store the token. When the token's variable is set, init asks the
    service (`GET /api/v1/project`) and refuses, writing nothing, unless the token is that project's and this folder's
    `origin` remote is the project's repository (https and ssh forms compare equal). An existing `central` section is never
    changed; when it is enabled, the same check runs. Without those options and without --local, init refuses.
  * `.auto/queue.md`: created only if absent. `.gitignore`: appends missing lines.
  * Reports an existing project `auto` command or auto_run scripts; never touches them.
Everything it adds is recorded in `.auto/init-manifest.json`, so --remove takes away only that. Running it again changes nothing.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


autocfg = _load('autorunner_cfg', 'autocfg.py')
autosafe = _load('autorunner_safe', 'autosafe.py')
autocentral = _load('autorunner_central', 'autocentral.py')

MANIFEST = Path('.auto') / 'init-manifest.json'
GITIGNORE_LINES = ['.auto/*', '!.auto/queue.md']
QUEUE_TEMPLATE = """# Auto queue

Work the auto-runner may do without a person, in order, one item per iteration. Each item needs an ID not used before, a sentence or two
of what to build, and an *Acceptance:* line that a command or test can prove. Tick the box in the same commit that finishes the item.

## Needs a person (the runner never does these)
- Push, merge, deploy, or anything outside this repository.
- Decisions about what the product should do.
"""
RULES_TEMPLATE = """# Rules for unattended runs

- Work only inside this repository's worktree. Never push, add a remote, merge, deploy or read `.env` files.
- One queue item per iteration. It is finished only when its acceptance line holds, shown by a command or test, and the checks pass.
- Never loosen or skip a test to get green. If blocked, write the question in the item's status line and stop.
- Use synthetic data only. Do not invent facts, results or figures.
"""


def detect_test_command(project):
    """-> (command, reason) or ('', reason). A proposal: the person confirms it by applying and may edit .claude/auto.json."""
    p = Path(project)
    pkg = p / 'package.json'
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text(encoding='utf-8')).get('scripts', {})
        except (OSError, ValueError, AttributeError):
            scripts = {}
        if isinstance(scripts, dict) and scripts.get('test'):
            return 'npm test', 'package.json has a "test" script'
    if any((p / f).exists() for f in ('pytest.ini', 'conftest.py', 'tox.ini')) or (
            (p / 'pyproject.toml').exists() and 'pytest' in (p / 'pyproject.toml').read_text(encoding='utf-8', errors='replace')) or (
            (p / 'tests').is_dir() and any((p / 'tests').glob('test_*.py'))):
        return 'python -m pytest -q', 'pytest files found'
    if any(p.glob('*.sln')) or any(p.glob('*.csproj')) or any(p.glob('*/*.csproj')):
        return 'dotnet test', 'a .NET solution or project found'
    if (p / 'go.mod').exists():
        return 'go test ./...', 'go.mod found'
    if (p / 'Cargo.toml').exists():
        return 'cargo test', 'Cargo.toml found'
    return '', 'no known test setup found'


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _read_manifest(project):
    path = project / MANIFEST
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {}


def _empty_manifest():
    return {'files': {}, 'settings_created': False, 'settings_rules': [], 'gitignore_lines': []}


CENTRAL_REQUIRED = ('The central service is the default: pass --central-url (the service\'s address) and --central-project (the '
                    'project\'s slug there), or --local to answer blockers on this machine only.')


def _raw_config(project):
    try:
        data = json.loads((project / autocfg.CONFIG_PATH).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _url_ok(url):
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    local = parts.hostname in ('localhost', '127.0.0.1', '::1')
    return bool(parts.hostname) and (parts.scheme == 'https' or (parts.scheme == 'http' and local)) and not (
        parts.username or parts.password or parts.query or parts.fragment)


def _verify(project, url, slug, name, env, send):
    """-> (notes, problems, steps): ask the service about the token in `name`, when it is set."""
    origin = autocentral.remote_url(project)
    token = (env.get(name) or '').strip()
    if not token:
        steps = autocentral.token_steps(url, slug, name, origin)
        return [f'Central: the token is not in {name} yet, so the repository check waits until it is; '
                'run init again after these steps.'], [], steps
    try:
        info = autocentral.lookup(url, token, send=send)
    except autocentral.autodeliver.TransportRefused as e:
        return [], [f'the central service refused the token in {name} ({e}). Make a new token on '
                    f'{url.rstrip("/")}/projects/{slug}/tokens and store it again.'], []
    except autocentral.autodeliver.TransportUnavailable as e:
        return [], [f'the central service at {url} could not be reached ({e}). Try again.'], []
    problems = autocentral.check(info, slug, origin)
    if problems:
        return [], problems, []
    return [f'Central: the token in {name} is project "{slug}"\'s, and origin is its repository ({info["repo_url"]}).'], [], []


def central_plan(project, central, env=None, send=None):
    """-> (section to add or None, notes, problems, steps). `central` is None (not asked), {'local': True}, or
    {'url', 'project', 'token_env'}. `env` (default os.environ) and `send` (an HTTP sender) are for tests."""
    env = os.environ if env is None else env
    central = central or {}
    existing = _raw_config(project).get('central')
    if existing is not None:
        if not isinstance(existing, dict) or existing.get('enabled') is not True:
            return None, ['.claude/auto.json already has a "central" section with the service off; it is left as it is.'], [], []
        url, slug = (existing.get('url') or '').strip(), (existing.get('project') or '').strip()
        name = autosafe.token_env({'central': existing})
        if not url or not slug:
            return None, [], ['.claude/auto.json has "central.enabled" without "central.url" and "central.project"; set them.'], []
        notes, problems, steps = _verify(project, url, slug, name, env, send)
        return None, ['.claude/auto.json already has a "central" section; it is left as it is.'] + notes, problems, steps
    if central.get('local'):
        return None, ['Local mode (--local): blockers are answered on this machine; no central section is written.'], [], []
    url, slug = (central.get('url') or '').strip().rstrip('/'), (central.get('project') or '').strip().lower()
    if not url and not slug:
        return None, [], [CENTRAL_REQUIRED], []
    problems = []
    if not _url_ok(url):
        problems.append('--central-url must be an https:// address (http only for localhost), with no user, query or fragment.')
    if not autocentral.SLUG.match(slug):
        problems.append('--central-project must be the project\'s slug: lower-case letters, digits and dashes.')
    name = (central.get('token_env') or '').strip() or autocentral.default_token_env(slug)
    if name.startswith(autosafe.CENTRAL_TOKEN_PREFIX):
        problems.append('--token-env takes the NAME of an environment variable, not the token. Replace (rotate) that token.')
    elif not autosafe.ENV_NAME.match(name):
        problems.append('--token-env must be an environment variable name (letters, digits and _).')
    if problems:
        return None, [], problems, []
    notes, problems, steps = _verify(project, url, slug, name, env, send)
    section = {'enabled': True, 'url': url, 'project': slug, 'token_env': name}
    return section, notes, problems, steps


def make_plan(project, central=None, env=None, send=None, allow_env_read=False):
    """-> dict describing what --apply would write. Reads only (the central check is a GET).
    `central`: see central_plan; None means neither the service nor --local was chosen, which is refused.
    `allow_env_read` (--allow-env-read) writes safety.allow_env_read into a new auto.json and leaves out the .env read rules."""
    project = Path(project).resolve()
    notes, problems = [], []
    try:
        base = autocfg.detect_base_branch(project)
    except autocfg.BaseBranchUnknown as e:
        base = ''
        notes.append(f'{e} The checkout/switch deny rules are skipped until the base branch is known.')
    settings, err = autosafe.load_settings(project)
    if err:
        problems.append(err)
    have = set(autosafe.deny_list(settings))
    cfg = autocfg.load(project) if not problems else dict(autocfg.DEFAULTS)
    auto_json_exists = (project / autocfg.CONFIG_PATH).exists()
    env_read = autosafe.env_read_allowed(cfg) or (allow_env_read and not auto_json_exists)
    if allow_env_read and auto_json_exists and not autosafe.env_read_allowed(cfg):
        notes.append('--allow-env-read only applies to a new .claude/auto.json: add "safety": {"allow_env_read": true} '
                     'to the existing one yourself.')
    rules = [r for r in autosafe.required_rules(base, autosafe.changelog_enabled(cfg), env_read) if r not in have]
    settings_exists = (project / autosafe.SETTINGS_PATH).exists()

    cmd, why = detect_test_command(project)
    auto_json = project / autocfg.CONFIG_PATH
    new_cfg = None
    if not auto_json.exists():
        new_cfg = {}
        if cmd:
            new_cfg['test_command'] = cmd
        else:
            new_cfg['check_only'] = True
            notes.append(f'{why}: auto.json will say check_only (results are "{autosafe.NO_TESTS}") until you set test_command.')
        if (project / 'CLAUDE.md').exists():
            new_cfg['rules_files'] = ['CLAUDE.md']
        if env_read:
            new_cfg['safety'] = {'allow_env_read': True}
            notes.append('--allow-env-read: the agent may read .env files, so their values can reach its transcript.')
        if (project / '.env').is_file():
            env_problems = autosafe.env_file_problems(project, {'env_files': ['.env']})
            if env_problems:
                notes.append('.env is not copied into run worktrees: ' + env_problems[0])
            else:
                new_cfg['env_files'] = ['.env']
                notes.append('env_files: .env is copied into the worktree of each run so tests use it; its values are '
                             'redacted from logs and events. Prefer a file with test settings over production ones.')
    else:
        notes.append('.claude/auto.json exists and is left as it is' + (f' (detected test command would be: {cmd})' if cmd else ''))
    rules_file = None
    if not (project / 'CLAUDE.md').exists():
        rules_file = project / 'AUTO_RULES.md'
        if rules_file.exists():
            rules_file = None
        else:
            notes.append('No CLAUDE.md: a short AUTO_RULES.md is available with --with-rules.')

    queue_path = project / cfg['queue_file']
    gi = project / '.gitignore'
    gi_have = set(gi.read_text(encoding='utf-8').splitlines()) if gi.exists() else set()
    gi_add = [x for x in GITIGNORE_LINES if x not in gi_have]

    central_add, central_notes, central_problems, central_steps = (
        central_plan(project, central, env, send) if not problems else (None, [], [], []))
    if central_add is not None and new_cfg is not None:
        new_cfg['central'] = central_add

    found = []
    if (project / '.claude' / 'commands' / 'auto.md').exists():
        found.append('.claude/commands/auto.md (project `auto` command)')
    for f in sorted((project / 'scripts').glob('auto_run*')) if (project / 'scripts').is_dir() else []:
        found.append(f'scripts/{f.name}')
    return {'project': project, 'base': base, 'rules': rules, 'settings_exists': settings_exists, 'new_cfg': new_cfg,
            'test_cmd': cmd, 'rules_file': rules_file, 'queue_path': queue_path if not queue_path.exists() else None,
            'gitignore_add': gi_add, 'existing': found, 'notes': notes, 'problems': problems,
            'central_add': central_add if new_cfg is None else None, 'central_notes': central_notes,
            'central_problems': central_problems, 'central_steps': central_steps}


def describe(plan):
    lines = []
    if plan['problems']:
        lines += [f'PROBLEM: {p}' for p in plan['problems']]
        lines.append('Fix that file by hand first; init will not overwrite it.')
        return lines
    if plan['central_problems']:
        lines += [f'REFUSED: {p}' for p in plan['central_problems']]
        lines.append('Nothing was written.')
        return lines
    lines.append(f'Base branch: {plan["base"] or "unknown"}')
    s = 'create' if not plan['settings_exists'] else 'append to'
    lines.append(f'.claude/settings.json: {s} deny rules: ' + (', '.join(plan['rules']) if plan['rules'] else 'none missing'))
    if plan['new_cfg'] is not None:
        lines.append(f'.claude/auto.json: create {json.dumps(plan["new_cfg"])}')
    elif plan['central_add'] is not None:
        lines.append(f'.claude/auto.json: add "central": {json.dumps(plan["central_add"])}')
    else:
        lines.append('.claude/auto.json: unchanged')
    lines.append('queue: ' + (f'create {plan["queue_path"].relative_to(plan["project"]).as_posix()}' if plan['queue_path'] else 'exists'))
    lines.append('.gitignore: ' + (('add ' + ', '.join(plan['gitignore_add'])) if plan['gitignore_add'] else 'nothing to add'))
    for f in plan['existing']:
        lines.append(f'Found, left untouched: {f}')
    lines += plan['notes'] + plan['central_notes']
    if plan['central_steps']:
        lines.append('To connect this project to the central service:')
        lines += ['  ' + s for s in plan['central_steps']]
    return lines


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def apply(plan, with_rules=False):
    """Write the plan. -> list of what changed (empty when there was nothing to do)."""
    if plan['problems'] or plan['central_problems']:
        raise SystemExit('Not applied: ' + '; '.join(plan['problems'] + plan['central_problems']))
    project = plan['project']
    man = _read_manifest(project) or _empty_manifest()
    for k, v in _empty_manifest().items():
        man.setdefault(k, v)
    changed = []

    if plan['rules']:
        settings, _ = autosafe.load_settings(project)
        if not (project / autosafe.SETTINGS_PATH).exists():
            man['settings_created'] = True
        perms = settings.setdefault('permissions', {})
        deny = perms.setdefault('deny', [])
        for r in plan['rules']:
            if r not in deny:
                deny.append(r)
                if r not in man['settings_rules']:
                    man['settings_rules'].append(r)
        _write(project / autosafe.SETTINGS_PATH, json.dumps(settings, indent=2) + '\n')
        changed.append('settings.json: ' + ', '.join(plan['rules']))

    def create(path, text):
        _write(path, text)
        man['files'][path.relative_to(project).as_posix()] = _sha(text)
        changed.append(f'created {path.relative_to(project).as_posix()}')

    cfg_data = plan['new_cfg']
    if with_rules and plan['rules_file']:
        create(plan['rules_file'], RULES_TEMPLATE)
        if cfg_data is not None:
            cfg_data['rules_files'] = ['AUTO_RULES.md']
    if cfg_data is not None:
        create(project / autocfg.CONFIG_PATH, json.dumps(cfg_data, indent=2) + '\n')
    if plan['central_add'] is not None:
        path = project / autocfg.CONFIG_PATH
        data = json.loads(path.read_text(encoding='utf-8'))
        if 'central' not in data:
            data['central'] = plan['central_add']
            _write(path, json.dumps(data, indent=2) + '\n')
            man['auto_json_central'] = plan['central_add']
            changed.append('.claude/auto.json: added "central"')
    if plan['queue_path']:
        create(plan['queue_path'], QUEUE_TEMPLATE)
    if plan['gitignore_add']:
        gi = project / '.gitignore'
        text = gi.read_text(encoding='utf-8') if gi.exists() else ''
        if text and not text.endswith('\n'):
            text += '\n'
        _write(gi, text + '\n'.join(plan['gitignore_add']) + '\n')
        man['gitignore_lines'] += [x for x in plan['gitignore_add'] if x not in man['gitignore_lines']]
        changed.append('.gitignore: ' + ', '.join(plan['gitignore_add']))
    if changed:
        _write(project / MANIFEST, json.dumps(man, indent=2) + '\n')
    return changed


def remove_plan(project):
    """-> (actions, manifest). Each action is (kind, detail). Only what the manifest says init added."""
    project = Path(project).resolve()
    man = _read_manifest(project)
    actions = []
    if not man:
        return actions, man
    settings, _ = autosafe.load_settings(project)
    present = set(autosafe.deny_list(settings))
    for r in man.get('settings_rules', []):
        if r in present:
            actions.append(('rule', r))
    added = man.get('auto_json_central')
    if added is not None and _raw_config(project).get('central') == added:
        actions.append(('central', '"central" in .claude/auto.json'))
    for rel, sha in man.get('files', {}).items():
        path = project / rel
        if not path.exists():
            continue
        same = _sha(path.read_text(encoding='utf-8')) == sha
        actions.append(('file' if same else 'keep', rel))
    gi = project / '.gitignore'
    have = set(gi.read_text(encoding='utf-8').splitlines()) if gi.exists() else set()
    for line in man.get('gitignore_lines', []):
        if line in have:
            actions.append(('ignore', line))
    return actions, man


def remove(project):
    project = Path(project).resolve()
    actions, man = remove_plan(project)
    done = []
    rules = {d for k, d in actions if k == 'rule'}
    if rules:
        settings, _ = autosafe.load_settings(project)
        settings['permissions']['deny'] = [r for r in settings['permissions']['deny'] if r not in rules]
        path = project / autosafe.SETTINGS_PATH
        empty = not settings['permissions']['deny'] and list(settings) == ['permissions'] and list(settings['permissions']) == ['deny']
        if man.get('settings_created') and empty:
            path.unlink()
            done.append('removed .claude/settings.json (init created it and nothing else is in it)')
        else:
            _write(path, json.dumps(settings, indent=2) + '\n')
            done.append('removed deny rules: ' + ', '.join(sorted(rules)))
    if any(k == 'central' for k, _ in actions):
        path = project / autocfg.CONFIG_PATH
        data = json.loads(path.read_text(encoding='utf-8'))
        data.pop('central', None)
        _write(path, json.dumps(data, indent=2) + '\n')
        done.append('removed "central" from .claude/auto.json')
    for kind, rel in actions:
        if kind == 'file':
            (project / rel).unlink()
            done.append(f'removed {rel}')
    lines = {d for k, d in actions if k == 'ignore'}
    if lines:
        gi = project / '.gitignore'
        keep = [ln for ln in gi.read_text(encoding='utf-8').splitlines() if ln not in lines]
        _write(gi, '\n'.join(keep) + ('\n' if keep else ''))
        done.append('removed .gitignore lines: ' + ', '.join(sorted(lines)))
    if man:
        (project / MANIFEST).unlink()
        done.append(f'removed {MANIFEST.as_posix()}')
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', default=os.environ.get('CLAUDE_PROJECT_DIR') or '.')
    ap.add_argument('--apply', action='store_true', help='write the changes (default: preview only)')
    ap.add_argument('--with-rules', action='store_true', help='write AUTO_RULES.md when the project has no CLAUDE.md')
    ap.add_argument('--remove', action='store_true', help='take away what init added (preview unless --apply)')
    ap.add_argument('--central-url', default='', help='the central service, e.g. https://auto-runner-central.vercel.app')
    ap.add_argument('--central-project', default='', metavar='SLUG', help="the project's slug on the central service")
    ap.add_argument('--token-env', default='', help='the variable that holds the token (default AUTO_RUNNER_TOKEN_<SLUG>)')
    ap.add_argument('--local', action='store_true', help='no central service: answer blockers on this machine')
    ap.add_argument('--allow-env-read', action='store_true', help='let the agent read .env files (no Read(./.env) rules)')
    a = ap.parse_args(argv)
    project = Path(a.project).resolve()
    if a.remove:
        actions, man = remove_plan(project)
        if not man:
            print('Nothing recorded: init has not added anything here.')
            return 0
        for kind, detail in actions:
            print({'rule': 'remove deny rule', 'file': 'remove file', 'ignore': 'remove .gitignore line',
                   'keep': 'KEEP (edited since init)'}[kind] + f': {detail}')
        if a.apply:
            for line in remove(project):
                print(line)
        else:
            print('Preview only. Add --apply to remove.')
        return 0
    central = {'local': True} if a.local else (
        {'url': a.central_url, 'project': a.central_project, 'token_env': a.token_env}
        if a.central_url or a.central_project else None)
    plan = make_plan(project, central, allow_env_read=a.allow_env_read)
    for line in describe(plan):
        print(line)
    if plan['problems'] or plan['central_problems']:
        return 1
    if not a.apply:
        print('Preview only. Add --apply to write.')
        return 0
    changed = apply(plan, a.with_rules)
    print('Done.' if changed else 'Nothing to do: already set up.')
    for c in changed:
        print(f'  {c}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
