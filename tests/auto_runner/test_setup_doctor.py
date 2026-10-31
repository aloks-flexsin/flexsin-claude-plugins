"""auto-runner plugin: capability registry and the read-only doctor (fake probes; one real run on a scratch folder)."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autorunner_setup_t', BIN / 'autosetup.py')
su = importlib.util.module_from_spec(spec)
sys.modules['autorunner_setup_t'] = su
spec.loader.exec_module(su)

EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
WIN_ENV = {'ProgramFiles(x86)': r'C:\Program Files (x86)', 'ProgramFiles': r'C:\Program Files',
           'LOCALAPPDATA': r'C:\Users\t\AppData\Local'}


class FakeProbe:
    def __init__(self, files=None, which=None, dirs=None, env=None, platform='win32'):
        self.files = dict(files or {})
        self.which_map = dict(which or {})
        self.dirs = dict(dirs or {})
        self.env_map = dict(WIN_ENV if env is None else env)
        self.platform = platform
        self.calls = []

    def exists(self, path):
        self.calls.append(('exists', path))
        return path in self.files or path in self.dirs

    def listdir(self, path):
        return list(self.dirs.get(path, []))

    def read_text(self, path):
        return self.files.get(path)

    def which(self, name):
        return self.which_map.get(name)

    def env(self, name):
        return self.env_map.get(name)


def full_probe(**over):
    files = {EDGE: '', str(BIN / 'automail.py'): '',
             'package.json': json.dumps({'devDependencies': {'@playwright/test': '^1.48.0'}}),
             '.gitignore': '.auto/*\n!.auto/queue.md\n',
             'CHANGELOG.md': '# Changelog\n\n## [Unreleased]\n',
             'docs/doc-map.json': '{"entries": []}'}
    files.update(over.pop('files', {}))
    return FakeProbe(files={k: v for k, v in files.items() if v is not None}, **over)


def by_id(results):
    return {r['id']: r for r in results}


def test_registry_ids_unique_and_expected():
    ids = [c.id for c in su.CAPABILITIES]
    assert len(ids) == len(set(ids))
    assert set(ids) == {'browser', 'playwright', 'browser_deps', 'mail_sink', 'accounts_ignore', 'changelog', 'doc_map'}


def test_everything_present_reports_ok_with_reasons():
    res = su.doctor(full_probe())
    for r in res:
        assert r['status'] == su.OK, r
        assert r['reason'] and r['repair'] == ''
    assert su.failed(res) == []


def test_everything_absent_reports_missing_with_reason_and_repair():
    res = by_id(su.doctor(FakeProbe(env={}, platform='win32')))
    for cid in ('browser', 'playwright', 'mail_sink', 'accounts_ignore', 'changelog', 'doc_map'):
        assert res[cid]['status'] == su.MISSING, res[cid]
        assert res[cid]['reason'] and res[cid]['repair'], res[cid]
    assert res['browser_deps']['status'] == su.OK                                       # not needed on Windows


def test_installed_edge_needs_no_download():
    r = by_id(su.doctor(full_probe()))['browser']
    assert r['status'] == su.OK
    assert 'Microsoft Edge' in r['reason'] and 'no download needed' in r['reason']
    assert 'install' not in r['reason'].lower()


def test_chrome_on_path_and_playwright_cache_count_as_present():
    p = full_probe(files={EDGE: None}, which={'google-chrome': '/usr/bin/google-chrome'}, platform='linux', env={'HOME': '/home/t'})
    assert 'Google Chrome' in by_id(su.doctor(p))['browser']['reason']
    p = full_probe(files={EDGE: None}, dirs={r'C:\Users\t\AppData\Local\ms-playwright': ['chromium-1140', 'ffmpeg-1010']})
    r = by_id(su.doctor(p))['browser']
    assert r['status'] == su.OK and 'already downloaded' in r['reason']


def test_missing_browser_reports_playwright_chromium_fallback():
    r = by_id(su.doctor(full_probe(files={EDGE: None})))['browser']
    assert r['status'] == su.MISSING
    assert 'playwright install chromium' in r['repair'] and 'estimate' in r['repair']


def test_admin_only_item_is_optional_not_a_failure():
    p = full_probe(files={EDGE: None}, which={'chromium': '/usr/bin/chromium'}, platform='linux', env={'HOME': '/home/t'})
    res = su.doctor(p)
    r = by_id(res)['browser_deps']
    assert r['status'] == su.OPTIONAL_ADMIN and 'administrator' in r['reason'] and r['repair']
    assert su.failed(res) == []
    assert '1 optional (administrator)' in su.describe(res)
    p.files['/usr/lib/x86_64-linux-gnu/libnss3.so'] = ''
    assert by_id(su.doctor(p))['browser_deps']['status'] == su.OK


def test_playwright_package_sources():
    def pw(files):
        return by_id(su.doctor(FakeProbe(files=files)))['playwright']
    assert pw({'package.json': '{"dependencies": {"playwright": "1.48"}}'})['status'] == su.OK
    assert pw({'requirements-dev.txt': 'pytest\nplaywright==1.48\n'})['reason'] == 'playwright in requirements-dev.txt'
    assert pw({'pyproject.toml': 'dependencies = ["playwright>=1.4"]'})['status'] == su.OK
    r = pw({'package.json': '{"devDependencies": {"jest": "29"}}'})
    assert r['status'] == su.MISSING and '@playwright/test' in r['repair']
    r = pw({'package.json': '{not json'})
    assert r['status'] == su.MISSING and 'not valid JSON' in r['reason']
    r = pw({'requirements.txt': 'pytest-playwrightish\n# playwright later\n'})
    assert r['status'] == su.MISSING and 'requirements-dev.txt' in r['repair']


def test_accounts_ignore_rules():
    def ig(text):
        return by_id(su.doctor(full_probe(files={'.gitignore': text})))['accounts_ignore']['status']
    assert ig('.auto/run/\n') == su.OK
    assert ig('/.auto/\n') == su.OK
    assert ig('node_modules\n') == su.MISSING
    assert ig('.auto/*\n!.auto/run/\n') == su.MISSING                                   # a later negation un-ignores it
    assert by_id(su.doctor(full_probe(files={'.gitignore': None})))['accounts_ignore']['reason'] == 'no .gitignore'


def test_changelog_and_doc_map_contents_are_checked():
    r = by_id(su.doctor(full_probe(files={'CHANGELOG.md': '# Changelog\n## [1.0.0]\n'})))['changelog']
    assert r['status'] == su.MISSING and 'Unreleased' in r['reason']
    for bad, why in (('{oops', 'not valid JSON'), ('[1, 2]', 'not a JSON object')):
        r = by_id(su.doctor(full_probe(files={'docs/doc-map.json': bad})))['doc_map']
        assert r['status'] == su.MISSING and why in r['reason']


def test_mail_sink_accepts_mailpit_on_path():
    p = full_probe(files={str(BIN / 'automail.py'): None}, which={'mailpit': '/usr/local/bin/mailpit'})
    assert by_id(su.doctor(p))['mail_sink']['status'] == su.OK
    p = full_probe(files={str(BIN / 'automail.py'): None})
    assert by_id(su.doctor(p))['mail_sink']['status'] == su.MISSING


def test_cli_prints_list_and_exit_code(capsys, tmp_path):
    assert su.main(['doctor', '--project', str(tmp_path)], probe=full_probe()) == 0
    out = capsys.readouterr().out
    assert '[ok] browser:' in out and '7 ok, 0 missing' in out
    assert su.main(['doctor', '--project', str(tmp_path)], probe=full_probe(files={'CHANGELOG.md': None})) == 1
    out = capsys.readouterr().out
    assert '[missing] changelog: no CHANGELOG.md' in out and 'repair: create CHANGELOG.md' in out
    assert su.main(['doctor', '--project', str(tmp_path), '--json'], probe=full_probe()) == 0
    assert len(json.loads(capsys.readouterr().out)) == len(su.CAPABILITIES)


def _snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob('*'))}


def test_doctor_writes_nothing_with_the_real_probe(tmp_path):
    (tmp_path / 'package.json').write_text('{"devDependencies": {"jest": "29"}}', encoding='utf-8')
    (tmp_path / '.gitignore').write_text('node_modules\n', encoding='utf-8')
    (tmp_path / 'docs').mkdir()
    before = _snapshot(tmp_path)
    proc = subprocess.run([sys.executable, '-B', str(BIN / 'autosetup.py'), 'doctor', '--project', str(tmp_path)],
                          capture_output=True, text=True, cwd=str(tmp_path))
    assert proc.returncode == 1, proc.stderr                                            # changelog, doc map, ignore are missing
    assert '[missing] changelog' in proc.stdout and '[missing] accounts_ignore' in proc.stdout
    assert _snapshot(tmp_path) == before


def test_not_a_directory_is_refused(tmp_path, capsys):
    assert su.main(['doctor', '--project', str(tmp_path / 'nope')]) == 2
