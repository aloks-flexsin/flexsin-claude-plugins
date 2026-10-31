"""auto-runner plugin: setup plan, one-approval apply, remove, and the unattended preflight guard (fake installer, real temp folders)."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner' / 'bin'


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, BIN / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


su = _load('autorunner_setup_apply_t', 'autosetup.py')
safe = _load('autorunner_safe_apply_t', 'autosafe.py')

EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
WIN_ENV = {'ProgramFiles(x86)': r'C:\Program Files (x86)', 'ProgramFiles': r'C:\Program Files',
           'LOCALAPPDATA': r'C:\Users\t\AppData\Local'}
MAIL = str(BIN / 'automail.py')


class Probe(su.SystemProbe):
    """Real project files; the machine (browser, mail sink, PATH, platform) is fake."""

    def __init__(self, project, installed=()):
        super().__init__(project)
        self.platform = 'win32'
        self.installed = set(installed)

    def exists(self, path):
        if Path(path).is_absolute() or path == MAIL:                                       # the machine, never the real one
            return path in self.installed
        return super().exists(path)

    def listdir(self, path):
        return []

    def which(self, name):
        return None

    def env(self, name):
        return WIN_ENV.get(name)


class FakeInstaller:
    """Records each command; 'installs' Edge into the probe unless told to fail."""

    def __init__(self, probe, code=0):
        self.probe, self.code, self.calls = probe, code, []

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        if self.code == 0 and 'install' in argv:
            self.probe.installed.add(EDGE)
        return self.code


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}


def dirs(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_dir())


@pytest.fixture
def project(tmp_path):
    (tmp_path / 'package.json').write_text('{\n  "name": "demo",\n  "devDependencies": {"jest": "29"}\n}\n', encoding='utf-8')
    (tmp_path / '.gitignore').write_bytes(b'node_modules\r\n')                               # CRLF kept exactly on restore
    (tmp_path / '.claude').mkdir()
    (tmp_path / '.claude' / 'settings.json').write_text(json.dumps(
        {'model': 'x', 'permissions': {'deny': ['Bash(git push:*)', 'Read(./.env)'], 'allow': ['Bash(ls)']}}, indent=2) + '\n',
        encoding='utf-8')
    return tmp_path


def by_id(rows):
    return {r['id']: r for r in rows}


def test_plan_lists_every_step_with_diffs_and_download(project):
    before = files(project)
    probe = Probe(project, installed={MAIL})
    plan = su.make_plan(project, probe)
    steps = {s['id']: s for s in plan['steps']}
    assert set(steps) == {'browser', 'playwright', 'accounts_ignore', 'changelog', 'doc_map', 'deny_rules'}
    assert steps['playwright']['action'] == 'edit' and '+    "@playwright/test": "^1.48.0"' in steps['playwright']['diff']
    assert steps['accounts_ignore']['diff'].rstrip().endswith('+.auto/run/')
    assert steps['changelog']['action'] == 'create' and '+## [Unreleased]' in steps['changelog']['diff']
    assert steps['deny_rules']['rules'] == [su.DENY_ACCOUNTS] and f'+      "{su.DENY_ACCOUNTS}"' in steps['deny_rules']['diff']
    b = steps['browser']
    assert b['action'] == 'command' and b['argv'] == ['npx', 'playwright', 'install', 'chromium']
    text = su.describe_plan(plan)
    assert 'download from: ' + su.DOWNLOAD_SOURCE in text and 'size: about 150 MB (estimate)' in text
    assert files(project) == before                                                        # plan writes nothing
    assert not any('.env' in s.get('path', '') for s in plan['steps'])


def test_installer_reported_size_replaces_the_estimate(project):
    plan = su.make_plan(project, Probe(project, installed={MAIL}), size_of=lambda argv: 162_000_000)
    s = next(s for s in plan['steps'] if s['id'] == 'browser')
    assert s['size'] == '162 MB (reported by the installer)' and 'estimate' not in s['size']


def test_download_is_not_run_without_apply(project, capsys):
    probe = Probe(project, installed={MAIL})
    inst = FakeInstaller(probe)
    assert su.main(['plan', '--project', str(project)], probe=probe, runner=inst) == 0
    out = capsys.readouterr().out
    assert 'would run: npx playwright install chromium' in out and 'size: about 150 MB (estimate)' in out
    assert inst.calls == []


def test_apply_performs_only_planned_steps_and_verifies(project):
    probe = Probe(project, installed={MAIL})
    plan = su.make_plan(project, probe)
    planned_paths = {s['path'] for s in plan['steps'] if 'path' in s}
    before = files(project)
    inst = FakeInstaller(probe)
    rows = su.apply(plan, inst, probe)
    assert all(r['status'] == 'done' for r in rows), rows
    assert inst.calls == [['npx', 'playwright', 'install', 'chromium']]
    after = files(project)
    changed = {k for k in after if before.get(k) != after[k]}
    assert changed == planned_paths | {'.auto/init-manifest.json'}
    assert su.failed(su.doctor(probe)) == []
    settings = json.loads((project / '.claude' / 'settings.json').read_text(encoding='utf-8'))
    assert settings['permissions']['deny'] == ['Bash(git push:*)', 'Read(./.env)', su.DENY_ACCOUNTS]   # only appended
    assert settings['model'] == 'x' and settings['permissions']['allow'] == ['Bash(ls)']


def test_second_apply_changes_nothing(project):
    probe = Probe(project, installed={MAIL})
    su.apply(su.make_plan(project, probe), FakeInstaller(probe), probe)
    snap = files(project)
    inst = FakeInstaller(probe)
    plan = su.make_plan(project, probe)
    assert plan['steps'] == [] and su.describe_plan(plan).startswith('Nothing to set up')
    assert su.apply(plan, inst, probe) == []
    assert files(project) == snap and inst.calls == []


def test_failed_step_reports_repair_and_rest_still_verify(project):
    probe = Probe(project, installed={MAIL})
    rows = by_id(su.apply(su.make_plan(project, probe), FakeInstaller(probe, code=1), probe))
    assert rows['browser']['status'] == 'failed' and 'exited with 1' in rows['browser']['detail']
    assert 'playwright install chromium' in rows['browser']['repair']
    for cid in ('playwright', 'accounts_ignore', 'changelog', 'doc_map', 'deny_rules'):
        assert rows[cid]['status'] == 'done', rows[cid]


def test_file_changed_since_preview_is_not_overwritten(project):
    probe = Probe(project, installed={MAIL, EDGE})
    plan = su.make_plan(project, probe)
    (project / '.gitignore').write_text('dist\n', encoding='utf-8')
    rows = by_id(su.apply(plan, FakeInstaller(probe), probe))
    assert rows['accounts_ignore']['status'] == 'failed' and 'changed since the preview' in rows['accounts_ignore']['detail']
    assert (project / '.gitignore').read_text(encoding='utf-8') == 'dist\n'


def test_admin_and_manual_steps_are_shown_never_run(project):
    probe = Probe(project, installed={EDGE})                                               # no mail sink: manual
    plan = su.make_plan(project, probe)
    mail = next(s for s in plan['steps'] if s['id'] == 'mail_sink')
    assert mail['action'] == 'manual'
    inst = FakeInstaller(probe)
    rows = by_id(su.apply(plan, inst, probe))
    assert rows['mail_sink']['status'] == 'shown' and inst.calls == []
    linux = Probe(project, installed={EDGE, MAIL})
    linux.platform = 'linux'
    step = next(s for s in su.make_plan(project, linux)['steps'] if s['id'] == 'browser_deps')
    assert step['action'] == 'admin' and 'administrator' in su.describe_plan({'steps': [step]})


def test_remove_restores_the_pre_setup_snapshot(project, capsys):
    snap, folders = files(project), dirs(project)
    probe = Probe(project, installed={MAIL})
    su.apply(su.make_plan(project, probe), FakeInstaller(probe), probe)
    assert files(project) != snap
    assert su.main(['remove', '--project', str(project)]) == 0
    out = capsys.readouterr().out
    assert 'restore to before setup: package.json' in out and 'cannot undo download' in out
    assert files(project) != snap                                                          # preview changed nothing
    assert su.main(['remove', '--project', str(project), '--apply']) == 0
    assert files(project) == snap and dirs(project) == folders


def test_remove_keeps_init_records_and_files_edited_since(project):
    (project / '.auto').mkdir()
    init_man = {'files': {'.auto/queue.md': 'abc'}, 'settings_created': False, 'settings_rules': [], 'gitignore_lines': []}
    (project / '.auto' / 'init-manifest.json').write_text(json.dumps(init_man), encoding='utf-8')
    probe = Probe(project, installed={MAIL, EDGE})
    su.apply(su.make_plan(project, probe), FakeInstaller(probe), probe)
    man = json.loads((project / '.auto' / 'init-manifest.json').read_text(encoding='utf-8'))
    assert man['files'] == init_man['files'] and 'setup' in man
    (project / 'CHANGELOG.md').write_text('# mine\n', encoding='utf-8')
    done = su.remove(project)
    assert 'kept CHANGELOG.md (edited since setup)' in done
    assert (project / 'CHANGELOG.md').read_text(encoding='utf-8') == '# mine\n'
    assert json.loads((project / '.auto' / 'init-manifest.json').read_text(encoding='utf-8')) == init_man


def test_existing_changelog_gets_the_heading_inserted(project):
    (project / 'CHANGELOG.md').write_bytes(b'# Changelog\n\n## [1.0.0]\n- first\n')
    probe = Probe(project, installed={MAIL, EDGE})
    step = next(s for s in su.make_plan(project, probe)['steps'] if s['id'] == 'changelog')
    assert step['action'] == 'edit' and step['after'] == '# Changelog\n\n## [Unreleased]\n\n## [1.0.0]\n- first\n'


def test_python_project_gets_requirements_line(tmp_path):
    (tmp_path / 'requirements-dev.txt').write_text('pytest', encoding='utf-8')
    probe = Probe(tmp_path, installed={MAIL})
    steps = {s['id']: s for s in su.make_plan(tmp_path, probe)['steps']}
    assert steps['playwright']['after'] == 'pytest\nplaywright\n'
    assert steps['browser']['argv'][1:] == ['-m', 'playwright', 'install', 'chromium']
    assert steps['deny_rules']['action'] == 'create'


def test_unusable_settings_file_is_never_rewritten(project):
    (project / '.claude' / 'settings.json').write_text('{broken', encoding='utf-8')
    plan = su.make_plan(project, Probe(project, installed={MAIL, EDGE}))
    assert not any(s['id'] == 'deny_rules' for s in plan['steps'])


# --- preflight guard -------------------------------------------------------------------------------------------------

def _repo(tmp_path):
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(tmp_path)], check=True)
    (tmp_path / '.claude').mkdir()
    (tmp_path / '.claude' / 'settings.json').write_text(json.dumps({'permissions': {'deny': safe.required_rules('main')}}),
                                                        encoding='utf-8')
    subprocess.run(['git', '-C', str(tmp_path), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=t', '-c', 'user.email=t@t.test', 'commit', '-qm', 'i'], check=True)
    return tmp_path


def _cfg(enabled):
    return {'test_command': 'pytest', 'check_only': False, 'acceptance': {'browser': {'enabled': enabled}}}


def _doctor(missing):
    return lambda project: [{'id': c.id, 'status': su.MISSING if c.id in missing else su.OK} for c in su.CAPABILITIES]


def test_preflight_refuses_when_browser_enabled_and_capability_missing(tmp_path):
    p = _repo(tmp_path)
    problems = safe.preflight(p, _cfg(True), 'main', doctor=_doctor({'browser', 'changelog'}))
    assert problems == ['acceptance.browser.enabled is true but the capability doctor reports missing: browser; '
                        'run /auto-runner:setup']


def test_preflight_passes_when_present_or_disabled(tmp_path):
    p = _repo(tmp_path)
    assert safe.preflight(p, _cfg(True), 'main', doctor=_doctor({'changelog'})) == []      # not needed for the browser
    called = []
    assert safe.preflight(p, _cfg(False), 'main', doctor=lambda pr: called.append(pr) or []) == []
    assert safe.preflight(p, {'test_command': 'pytest', 'check_only': False}, 'main',
                          doctor=lambda pr: called.append(pr) or []) == []
    assert called == []                                                                    # doctor never consulted when off


def test_preflight_uses_the_real_doctor_by_default(tmp_path):
    p = _repo(tmp_path)
    problems = safe.preflight(p, _cfg(True), 'main')
    assert any(x.endswith(safe.SETUP_HINT) and 'accounts_ignore' in x for x in problems)
