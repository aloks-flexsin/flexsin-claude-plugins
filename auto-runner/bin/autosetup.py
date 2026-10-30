"""
What a project needs before browser acceptance, the changelog and the doc checks can run, and what is missing (capability doctor).

    python autosetup.py doctor --project DIR [--json]     # read-only: one line per capability, plus the repair step per failure

Capabilities (the registry `CAPABILITIES`, in this order):
  * browser          an installed Microsoft Edge or Google Chrome (no download needed), else a Chromium Playwright already downloaded;
                     when none is found the repair is the Playwright Chromium download.
  * playwright       the Playwright package in `package.json` (`playwright` or `@playwright/test`) or in the Python requirements.
  * browser_deps     system libraries the browser needs on Linux; installing them needs administrator rights, so a gap is
                     `optional-admin`, never a failure. Not needed on Windows or macOS.
  * mail_sink        the in-process SMTP sink (`automail.py` next to this file) or a `mailpit`/`mailhog` on PATH.
  * accounts_ignore  `.gitignore` keeps `.auto/run/` (where throwaway test accounts are written) out of git.
  * changelog        `CHANGELOG.md` with an `## [Unreleased]` heading.
  * doc_map          `docs/doc-map.json` that parses as a JSON object.

Each `detect(probe)` returns {id, label, status, reason, repair}: status is `ok`, `missing` or `optional-admin`, the reason is
plain words and the repair is empty when nothing needs doing. Every look at the machine goes through the probe (file existence,
`shutil.which`, reading a file, environment, platform), so tests use a fake. Nothing here writes, downloads or installs anything.
Exit code of `doctor`: 0 when nothing is `missing` (optional-admin does not fail), else 1. Stdlib only.

Setting up (one preview, one approval):

    python autosetup.py plan   --project DIR [--json]     # read-only: every step, with a diff per file and any download
    python autosetup.py apply  --project DIR              # run exactly the steps the plan lists, then verify each with the doctor
    python autosetup.py remove --project DIR [--apply]    # preview, then take away only what setup added

`make_plan(project, probe)` turns each `missing` capability into a step: `create` / `edit` (a unified diff of the file:
CHANGELOG.md, docs/doc-map.json, `.gitignore`, `package.json` or `requirements-dev.txt`), `command` (the Playwright Chromium
download, with its source and a size marked "estimate" unless `size_of(argv)` reports bytes), `admin` (shown, never run) or
`manual` (shown, never run). It always adds the deny rule DENY_ACCOUNTS so the agent cannot read the throwaway accounts file;
deny rules are only ever appended. `apply(plan, runner, probe)` runs the plan's steps and nothing else (a file that changed
since the preview is refused, not overwritten), checks each with the doctor and records every file it wrote in
`.auto/init-manifest.json` under the key `setup` (the text before, the hash after), keeping init's own keys. `remove` restores
each recorded file to its text before setup when it has not been edited since (otherwise it is kept and named) and removes
folders setup created when empty. A download cannot be undone by `remove`; it says so. A second `apply` finds nothing missing
and changes nothing. Setup never reads or writes `.env` files or any secret and never removes or rewrites a deny rule.
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OK, MISSING, OPTIONAL_ADMIN = 'ok', 'missing', 'optional-admin'
CHROMIUM_FALLBACK = ('Playwright Chromium: `npx playwright install chromium` (or `python -m playwright install chromium`), '
                     'a download of about 150 MB (estimate)')
ACCOUNTS_DIR = '.auto/run/'
COVERS_ACCOUNTS = {'.auto', '.auto/', '.auto/*', '.auto/**', '.auto/run', '.auto/run/', '.auto/run/*', '.auto/run/**'}


class SystemProbe:
    """The real machine, read-only. Relative paths are resolved against the project."""

    def __init__(self, project):
        self.project = Path(project)
        self.platform = sys.platform

    def _path(self, path):
        p = Path(path)
        return p if p.is_absolute() else self.project / p

    def exists(self, path):
        try:
            return self._path(path).exists()
        except OSError:
            return False

    def listdir(self, path):
        try:
            return sorted(os.listdir(self._path(path)))
        except OSError:
            return []

    def read_text(self, path):
        try:
            with open(self._path(path), encoding='utf-8', errors='replace', newline='') as f:   # line endings kept, see _current
                return f.read()
        except OSError:
            return None

    def which(self, name):
        return shutil.which(name)

    def env(self, name):
        return os.environ.get(name)


def _result(cap, status, reason, repair=''):
    return {'id': cap.id, 'label': cap.label, 'status': status, 'reason': reason, 'repair': '' if status == OK else repair}


def _win_join(base, *parts):
    return '\\'.join([base.rstrip('\\/')] + list(parts))


def _browser_candidates(probe):
    """[(name, path)] of the places an installed Edge or Chrome lives, Edge first."""
    out = []
    if probe.platform.startswith('win'):
        roots = [probe.env(v) for v in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA')]
        for name, rel in (('Microsoft Edge', ('Microsoft', 'Edge', 'Application', 'msedge.exe')),
                          ('Google Chrome', ('Google', 'Chrome', 'Application', 'chrome.exe'))):
            out += [(name, _win_join(r, *rel)) for r in roots if r]
    elif probe.platform == 'darwin':
        out += [('Microsoft Edge', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'),
                ('Google Chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')]
    return out


def _playwright_cache(probe):
    if probe.platform.startswith('win'):
        base = probe.env('LOCALAPPDATA')
        return _win_join(base, 'ms-playwright') if base else None
    home = probe.env('HOME')
    if not home:
        return None
    return home.rstrip('/') + ('/Library/Caches/ms-playwright' if probe.platform == 'darwin' else '/.cache/ms-playwright')


class Browser:
    id, label = 'browser', 'browser for acceptance journeys'

    def detect(self, probe):
        for name, path in _browser_candidates(probe):
            if probe.exists(path):
                return _result(self, OK, f'{name} at {path} (no download needed)')
        for cmd, name in (('msedge', 'Microsoft Edge'), ('microsoft-edge', 'Microsoft Edge'),
                          ('microsoft-edge-stable', 'Microsoft Edge'), ('google-chrome', 'Google Chrome'),
                          ('google-chrome-stable', 'Google Chrome'), ('chrome', 'Google Chrome'),
                          ('chromium', 'Chromium'), ('chromium-browser', 'Chromium')):
            found = probe.which(cmd)
            if found:
                return _result(self, OK, f'{name} at {found} (no download needed)')
        cache = _playwright_cache(probe)
        if cache:
            got = [d for d in probe.listdir(cache) if d.startswith('chromium')]
            if got:
                return _result(self, OK, f'Playwright Chromium already downloaded ({cache}, {got[0]})')
        return _result(self, MISSING, 'no Microsoft Edge, Google Chrome or downloaded Chromium found',
                       'install the fallback ' + CHROMIUM_FALLBACK)


REQ_LINE = re.compile(r'^\s*playwright\b', re.I | re.M)
PYPROJECT_DEP = re.compile(r'["\']playwright\b', re.I)


class PlaywrightPackage:
    id, label = 'playwright', 'Playwright package'

    def detect(self, probe):
        pkg = probe.read_text('package.json')
        if pkg is not None:
            try:
                data = json.loads(pkg)
            except ValueError:
                return _result(self, MISSING, 'package.json is not valid JSON', 'fix package.json, then add @playwright/test to devDependencies')
            deps = {}
            for key in ('dependencies', 'devDependencies'):
                if isinstance(data, dict) and isinstance(data.get(key), dict):
                    deps.update(data[key])
            for name in ('@playwright/test', 'playwright'):
                if name in deps:
                    return _result(self, OK, f'{name} {deps[name]} in package.json')
        for req in ('requirements.txt', 'requirements-dev.txt'):
            text = probe.read_text(req)
            if text and REQ_LINE.search(text):
                return _result(self, OK, f'playwright in {req}')
        text = probe.read_text('pyproject.toml')
        if text and PYPROJECT_DEP.search(text):
            return _result(self, OK, 'playwright in pyproject.toml')
        if pkg is not None:
            return _result(self, MISSING, 'no playwright or @playwright/test in package.json',
                           'add @playwright/test to devDependencies (`npm install --save-dev @playwright/test`)')
        return _result(self, MISSING, 'no playwright in package.json, requirements or pyproject.toml',
                       'add playwright to requirements-dev.txt (`pip install playwright`)')


LINUX_BROWSER_LIBS = ('/usr/lib/x86_64-linux-gnu/libnss3.so', '/usr/lib64/libnss3.so', '/usr/lib/libnss3.so')


class BrowserDeps:
    id, label = 'browser_deps', 'browser system libraries'

    def detect(self, probe):
        if not probe.platform.startswith('linux'):
            return _result(self, OK, 'not needed on this operating system')
        for lib in LINUX_BROWSER_LIBS:
            if probe.exists(lib):
                return _result(self, OK, f'found {lib}')
        return _result(self, OPTIONAL_ADMIN, 'browser libraries (libnss3) not found; installing them needs administrator rights',
                       'ask an administrator to run `npx playwright install-deps chromium`')


class MailSink:
    id, label = 'mail_sink', 'mail sink for sign-up and verification mail'

    def detect(self, probe):
        sink = str(HERE / 'automail.py')
        if probe.exists(sink):
            return _result(self, OK, 'in-process sink automail.py (no Docker needed)')
        for cmd in ('mailpit', 'mailhog', 'MailHog'):
            found = probe.which(cmd)
            if found:
                return _result(self, OK, f'{cmd} at {found}')
        return _result(self, MISSING, 'the in-process mail sink (automail.py) is not in this plugin version and no mailpit or mailhog is on PATH',
                       'update the auto-runner plugin to a version with automail.py')


class AccountsIgnore:
    id, label = 'accounts_ignore', 'test accounts kept out of git'

    def detect(self, probe):
        text = probe.read_text('.gitignore')
        if text is None:
            return _result(self, MISSING, 'no .gitignore', f'add `{ACCOUNTS_DIR}` to .gitignore')
        lines = [ln.strip() for ln in text.splitlines()]
        covered = False
        for ln in lines:                                                                  # later negations win, as in git
            bare = ln.lstrip('/')
            if bare in COVERS_ACCOUNTS:
                covered = True
            elif ln.startswith('!') and ln[1:].lstrip('/') in COVERS_ACCOUNTS | {'.auto/run/accounts.json'}:
                covered = False
        if covered:
            return _result(self, OK, f'.gitignore covers {ACCOUNTS_DIR}')
        return _result(self, MISSING, f'.gitignore does not cover {ACCOUNTS_DIR}', f'add `{ACCOUNTS_DIR}` to .gitignore')


UNRELEASED = re.compile(r'^##\s*\[?unreleased\]?\s*$', re.I | re.M)


class Changelog:
    id, label = 'changelog', 'changelog scaffold'

    def detect(self, probe):
        text = probe.read_text('CHANGELOG.md')
        if text is None:
            return _result(self, MISSING, 'no CHANGELOG.md', 'create CHANGELOG.md with a `## [Unreleased]` heading (Keep a Changelog)')
        if not UNRELEASED.search(text):
            return _result(self, MISSING, 'CHANGELOG.md has no `## [Unreleased]` heading', 'add a `## [Unreleased]` heading to CHANGELOG.md')
        return _result(self, OK, 'CHANGELOG.md with an Unreleased section')


class DocMap:
    id, label = 'doc_map', 'doc map'
    path = 'docs/doc-map.json'

    def detect(self, probe):
        text = probe.read_text(self.path)
        if text is None:
            return _result(self, MISSING, f'no {self.path}', f'create {self.path} listing which docs describe which code paths')
        try:
            data = json.loads(text)
        except ValueError:
            return _result(self, MISSING, f'{self.path} is not valid JSON', f'fix {self.path}')
        if not isinstance(data, dict):
            return _result(self, MISSING, f'{self.path} is not a JSON object', f'fix {self.path}')
        return _result(self, OK, f'{self.path} present')


CAPABILITIES = (Browser(), PlaywrightPackage(), BrowserDeps(), MailSink(), AccountsIgnore(), Changelog(), DocMap())


def doctor(probe, capabilities=CAPABILITIES):
    return [c.detect(probe) for c in capabilities]


def failed(results):
    return [r for r in results if r['status'] == MISSING]


def describe(results):
    lines = []
    for r in results:
        lines.append(f"[{r['status']}] {r['id']}: {r['reason']}")
        if r['repair']:
            lines.append(f"    repair: {r['repair']}")
    bad = failed(results)
    opt = [r for r in results if r['status'] == OPTIONAL_ADMIN]
    lines.append(f'{len(results) - len(bad) - len(opt)} ok, {len(bad)} missing, {len(opt)} optional (administrator)')
    return '\n'.join(lines)


MANIFEST = Path('.auto') / 'init-manifest.json'
SETTINGS = Path('.claude') / 'settings.json'
DENY_ACCOUNTS = 'Read(./.auto/run/accounts.json)'
PLAYWRIGHT_NPM = '^1.48.0'
CHANGELOG_TEMPLATE = '# Changelog\n\nAll notable changes to this project are written here (Keep a Changelog).\n\n## [Unreleased]\n'
DOC_MAP_TEMPLATE = '{\n  "entries": []\n}\n'
DOWNLOAD_SOURCE = "Playwright's own browser download (`playwright install chromium`)"
DOWNLOAD_ESTIMATE = 'about 150 MB (estimate)'


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _diff(rel, before, after):
    return ''.join(difflib.unified_diff((before or '').splitlines(True), after.splitlines(True),
                                        fromfile='/dev/null' if before is None else f'a/{rel}', tofile=f'b/{rel}'))


def _file_step(cap, rel, before, after, detects):
    return {'id': cap, 'action': 'create' if before is None else 'edit', 'path': rel, 'before': before, 'after': after,
            'diff': _diff(rel, before, after), 'detects': detects}


def _with_line(text, line):
    text = text or ''
    if text and not text.endswith('\n'):
        text += '\n'
    return text + line + '\n'


def _changelog_after(text):
    if text is None:
        return CHANGELOG_TEMPLATE
    lines = text.splitlines(True)
    if lines and lines[0].startswith('# '):
        head = lines[0] if lines[0].endswith('\n') else lines[0] + '\n'
        return head + '\n## [Unreleased]\n' + ''.join(lines[1:])
    return '## [Unreleased]\n\n' + text


def _settings_after(text):
    """-> new settings text with DENY_ACCOUNTS appended, or None when it is already there or the file is not usable."""
    try:
        data = json.loads(text) if text is not None else {}
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    perms = data.setdefault('permissions', {})
    if not isinstance(perms, dict):
        return None
    deny = perms.setdefault('deny', [])
    if not isinstance(deny, list) or DENY_ACCOUNTS in deny:
        return None
    deny.append(DENY_ACCOUNTS)
    return json.dumps(data, indent=2) + '\n'


def _step_for(r, probe):
    """The step that repairs one `missing` or `optional-admin` result."""
    cap = r['id']
    if r['status'] == OPTIONAL_ADMIN:
        return {'id': cap, 'action': 'admin', 'detects': r['reason'], 'repair': r['repair']}
    if cap == 'browser':
        npm = probe.read_text('package.json') is not None
        argv = ['npx', 'playwright', 'install', 'chromium'] if npm else [sys.executable, '-m', 'playwright', 'install', 'chromium']
        return {'id': cap, 'action': 'command', 'argv': argv, 'source': DOWNLOAD_SOURCE, 'size': DOWNLOAD_ESTIMATE,
                'detects': r['reason'], 'repair': r['repair']}
    if cap == 'playwright':
        pkg = probe.read_text('package.json')
        if pkg is not None:
            try:
                data = json.loads(pkg)
            except ValueError:
                data = None
            if not isinstance(data, dict) or not isinstance(data.get('devDependencies', {}), dict):
                return {'id': cap, 'action': 'manual', 'detects': r['reason'], 'repair': 'fix package.json by hand, then run setup again'}
            data.setdefault('devDependencies', {})['@playwright/test'] = PLAYWRIGHT_NPM
            return _file_step(cap, 'package.json', pkg, json.dumps(data, indent=2) + '\n', r['reason'])
        req = probe.read_text('requirements-dev.txt')
        return _file_step(cap, 'requirements-dev.txt', req, _with_line(req, 'playwright'), r['reason'])
    if cap == 'accounts_ignore':
        gi = probe.read_text('.gitignore')
        return _file_step(cap, '.gitignore', gi, _with_line(gi, ACCOUNTS_DIR), r['reason'])
    if cap == 'changelog':
        text = probe.read_text('CHANGELOG.md')
        return _file_step(cap, 'CHANGELOG.md', text, _changelog_after(text), r['reason'])
    if cap == 'doc_map':
        text = probe.read_text(DocMap.path)
        if text is not None:
            return {'id': cap, 'action': 'manual', 'detects': r['reason'], 'repair': r['repair']}
        return _file_step(cap, DocMap.path, None, DOC_MAP_TEMPLATE, r['reason'])
    return {'id': cap, 'action': 'manual', 'detects': r['reason'], 'repair': r['repair']}


def make_plan(project, probe=None, size_of=None, capabilities=CAPABILITIES):
    """-> {'project', 'results', 'steps'}. Reads only. `size_of(argv) -> bytes or None` is what an installer reports."""
    project = Path(project).resolve()
    probe = probe or SystemProbe(project)
    results = doctor(probe, capabilities)
    steps = [_step_for(r, probe) for r in results if r['status'] != OK]
    for s in steps:
        if s['action'] == 'command' and size_of:
            size = size_of(s['argv'])
            if size:
                s['size'] = f'{size / 1_000_000:.0f} MB (reported by the installer)'
    text = probe.read_text(SETTINGS.as_posix())
    after = _settings_after(text)
    if after is not None:
        steps.append(_file_step('deny_rules', SETTINGS.as_posix(), text, after,
                                f'{SETTINGS.as_posix()} does not deny reading the test accounts file'))
        steps[-1]['rules'] = [DENY_ACCOUNTS]
    return {'project': project, 'results': results, 'steps': steps}


def describe_plan(plan):
    steps = plan['steps']
    if not steps:
        return 'Nothing to set up: every capability is present.'
    lines = []
    for s in steps:
        head = f"[{s['action']}] {s['id']}: {s['detects']}"
        if s['action'] in ('create', 'edit'):
            lines += [head, f"    would {s['action']} {s['path']}:"] + ['    ' + ln for ln in s['diff'].splitlines()]
        elif s['action'] == 'command':
            lines += [head, f"    would run: {' '.join(s['argv'])}", f"    download from: {s['source']}", f"    size: {s['size']}"]
        elif s['action'] == 'admin':
            lines += [head, f"    needs an administrator, not run: {s['repair']}"]
        else:
            lines += [head, f"    by hand, not run: {s['repair']}"]
    runs = sum(1 for s in steps if s['action'] in ('create', 'edit', 'command'))
    lines.append(f'{runs} step(s) would run; {len(steps) - runs} shown only.')
    return '\n'.join(lines)


def _read_manifest(project):
    try:
        data = json.loads((project / MANIFEST).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_text(project, rel, text, made_dirs):
    path = project / rel
    missing, d = [], path.parent
    while not d.exists():
        missing.append(d)
        d = d.parent
    for m in reversed(missing):
        m.mkdir()
        made_dirs.append(m.relative_to(project).as_posix())
    path.write_text(text, encoding='utf-8', newline='')


def _current(project, rel):
    try:
        with open(project / rel, encoding='utf-8', newline='') as f:              # keep CRLF as is, so a restore is exact
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def default_runner(argv, cwd):
    exe = shutil.which(argv[0]) or argv[0]
    return subprocess.run([exe, *argv[1:]], cwd=str(cwd)).returncode


def _verify(step, project, probe, capabilities):
    if step['id'] == 'deny_rules':
        return DENY_ACCOUNTS in (_current(project, SETTINGS.as_posix()) or ''), 'deny rule present'
    cap = next(c for c in capabilities if c.id == step['id'])
    r = cap.detect(probe)
    return r['status'] == OK, r['reason']


def apply(plan, runner=None, probe=None, capabilities=CAPABILITIES):
    """Run exactly the plan's steps. -> [{id, action, status: done|failed|shown, detail, repair}]."""
    project = plan['project']
    runner = runner or default_runner
    probe = probe or SystemProbe(project)
    man = _read_manifest(project)
    rec = man.get('setup') if isinstance(man.get('setup'), dict) else {}
    rec.setdefault('files', {})
    rec.setdefault('dirs', [])
    rec.setdefault('commands', [])
    out, wrote = [], False
    for s in plan['steps']:
        row = {'id': s['id'], 'action': s['action'], 'repair': s.get('repair', '')}
        if s['action'] in ('admin', 'manual'):
            out.append({**row, 'status': 'shown', 'detail': 'not run by setup'})
            continue
        try:
            if s['action'] == 'command':
                code = runner(s['argv'], project)
                rec['commands'].append(' '.join(s['argv']))
                wrote = True
                if code != 0:
                    out.append({**row, 'status': 'failed', 'detail': f'`{" ".join(s["argv"])}` exited with {code}'})
                    continue
            else:
                if _current(project, s['path']) != s['before']:
                    out.append({**row, 'status': 'failed', 'detail': f"{s['path']} changed since the preview; not overwritten",
                                'repair': 'run setup again to see a fresh plan'})
                    continue
                _write_text(project, s['path'], s['after'], rec['dirs'])
                entry = rec['files'].setdefault(s['path'], {'before': s['before']})
                entry['after_sha'] = _sha(s['after'])
                wrote = True
        except OSError as e:
            out.append({**row, 'status': 'failed', 'detail': f'{type(e).__name__}: {e}'})
            continue
        ok, why = _verify(s, project, probe, capabilities)
        out.append({**row, 'status': 'done' if ok else 'failed', 'detail': why if ok else f'doctor still reports: {why}'})
    if wrote:
        if not (project / MANIFEST.parent).exists() and MANIFEST.parent.as_posix() not in rec['dirs']:
            rec['dirs'].append(MANIFEST.parent.as_posix())
        man['setup'] = rec
        (project / MANIFEST.parent).mkdir(parents=True, exist_ok=True)
        (project / MANIFEST).write_text(json.dumps(man, indent=2) + '\n', encoding='utf-8')
    return out


def remove_plan(project):
    """-> [(kind, detail)]: kind restore | delete | keep | rmdir | download. Only what setup recorded."""
    project = Path(project).resolve()
    rec = _read_manifest(project).get('setup') or {}
    actions = []
    for rel, entry in rec.get('files', {}).items():
        now = _current(project, rel)
        if now is None:
            continue
        if _sha(now) != entry.get('after_sha'):
            actions.append(('keep', rel))
        else:
            actions.append(('delete' if entry.get('before') is None else 'restore', rel))
    for d in reversed(rec.get('dirs', [])):
        actions.append(('rmdir', d))
    for c in rec.get('commands', []):
        actions.append(('download', c))
    return actions


def remove(project):
    project = Path(project).resolve()
    man = _read_manifest(project)
    rec = man.get('setup') or {}
    done = []
    for kind, rel in remove_plan(project):
        path = project / rel
        if kind == 'restore':
            path.write_text(rec['files'][rel]['before'], encoding='utf-8', newline='')
            done.append(f'restored {rel}')
        elif kind == 'delete':
            path.unlink()
            done.append(f'removed {rel}')
        elif kind == 'keep':
            done.append(f'kept {rel} (edited since setup)')
        elif kind == 'download':
            done.append(f'not undone: `{rel}` (remove the downloaded browser from the Playwright cache by hand if wanted)')
    man.pop('setup', None)
    mpath = project / MANIFEST
    if man:
        mpath.write_text(json.dumps(man, indent=2) + '\n', encoding='utf-8')
    elif mpath.exists():
        mpath.unlink()
    for rel in reversed(rec.get('dirs', [])):
        try:
            (project / rel).rmdir()
            done.append(f'removed empty folder {rel}')
        except OSError:
            pass
    return done


REMOVE_LABEL = {'restore': 'restore to before setup', 'delete': 'remove file', 'keep': 'KEEP (edited since setup)',
                'rmdir': 'remove folder if empty', 'download': 'cannot undo download'}


def main(argv=None, probe=None, runner=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name, text in (('doctor', 'read-only check of every capability'), ('plan', 'read-only preview of the setup steps'),
                       ('apply', 'run the steps the plan lists'), ('remove', 'preview, or with --apply take away what setup added')):
        p = sub.add_parser(name, help=text)
        p.add_argument('--project', default=os.environ.get('CLAUDE_PROJECT_DIR') or '.')
        if name in ('doctor', 'plan'):
            p.add_argument('--json', action='store_true')
        if name == 'remove':
            p.add_argument('--apply', action='store_true')
    a = ap.parse_args(argv)
    project = Path(a.project)
    if not project.is_dir():
        print(f'not a directory: {project}', file=sys.stderr)
        return 2
    probe = probe or SystemProbe(project)
    if a.cmd == 'doctor':
        results = doctor(probe)
        print(json.dumps(results, indent=2) if a.json else describe(results))
        return 1 if failed(results) else 0
    if a.cmd == 'plan':
        plan = make_plan(project, probe)
        if a.json:
            print(json.dumps([{k: v for k, v in s.items() if k not in ('before', 'after')} for s in plan['steps']], indent=2))
        else:
            print(describe_plan(plan))
        return 0
    if a.cmd == 'apply':
        plan = make_plan(project, probe)
        print(describe_plan(plan))
        rows = apply(plan, runner, probe)
        for r in rows:
            print(f"[{r['status']}] {r['id']}: {r['detail']}" + (f" (repair: {r['repair']})" if r['status'] == 'failed' and r['repair'] else ''))
        return 1 if any(r['status'] == 'failed' for r in rows) else 0
    actions = remove_plan(project)
    if not actions:
        print('Nothing recorded: setup has not added anything here.')
        return 0
    if not a.apply:
        for kind, detail in actions:
            print(f'{REMOVE_LABEL[kind]}: {detail}')
        print('Preview only. Add --apply to remove.')
        return 0
    for line in remove(project):
        print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
