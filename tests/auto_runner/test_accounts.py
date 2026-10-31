"""auto-runner plugin: throwaway test accounts, redaction of generated credentials and the committed-content scan (no network)."""
import importlib.util
import json
import os
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


browser = _load('autorunner_browser_acc', 'autobrowser.py')
events = _load('autorunner_events_acc', 'autoevents.py')
from tests.auto_runner import test_runner as tr  # noqa: E402
from tests.auto_runner.test_runner import project  # noqa: E402,F401  (the scratch-repo fixture)

LOCAL = {'enabled': True, 'base_url': 'http://localhost:5000'}


def _repo(path, ignore='.auto/run/\n'):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(path)], check=True)
    if ignore is not None:
        (path / '.gitignore').write_text(ignore, encoding='utf-8')
    return path


def _all_text(root):
    """Every file under root (except the accounts file itself) as one string."""
    out = []
    for f in Path(root).rglob('*'):
        if f.is_file() and f.name != 'accounts.json' and '.git' not in f.parts:
            out.append(f.read_text(encoding='utf-8', errors='replace'))
    return '\n'.join(out)


def test_one_account_per_role_with_distinct_strong_passwords(tmp_path, capfd):
    p = _repo(tmp_path / 'p')
    got = browser.provision(p, ['Admin', 'editor', 'viewer', 'admin'], LOCAL)
    assert sorted(got['accounts']) == ['admin', 'editor', 'viewer'] and got['method'] == 'signup' and not got['seeded']
    pws = [a['password'] for a in got['accounts'].values()]
    emails = [a['email'] for a in got['accounts'].values()]
    assert len(set(pws)) == 3 and all(len(pw) >= 24 for pw in pws)
    assert len(set(emails)) == 3 and all(e.endswith('@' + browser.TEST_DOMAIN) and e.split('@')[0].startswith(r)
                                        for e, r in zip(emails, sorted(got['accounts'])))
    assert set(got['env']) == {f'AUTO_TEST_{r}_{k}' for r in ('ADMIN', 'EDITOR', 'VIEWER') for k in ('EMAIL', 'PASSWORD')}
    assert got['env']['AUTO_TEST_ADMIN_PASSWORD'] == got['accounts']['admin']['password']
    stored = json.loads((p / browser.ACCOUNTS_FILE).read_text(encoding='utf-8'))
    assert stored['accounts'] == got['accounts']
    assert browser.known_secrets(p, tmp_path / 'missing') == got['env']
    assert not any(k.startswith('AUTO_TEST_') for k in os.environ)               # never put into this process
    captured = capfd.readouterr()
    assert not any(v in captured.out + captured.err for v in got['env'].values())
    again = browser.provision(p, ['admin'], LOCAL)
    assert again['accounts']['admin']['password'] != got['accounts']['admin']['password']


@pytest.mark.skipif(os.name == 'nt', reason='POSIX file modes')
def test_accounts_file_is_owner_only(tmp_path):
    p = _repo(tmp_path / 'p')
    browser.provision(p, ['admin'], LOCAL)
    assert ((p / browser.ACCOUNTS_FILE).stat().st_mode & 0o777) == 0o600


def test_accounts_file_is_git_ignored_and_never_committed(tmp_path):
    p = _repo(tmp_path / 'p')
    browser.provision(p, ['admin'], LOCAL)
    assert subprocess.run(['git', '-C', str(p), 'check-ignore', '-q', browser.ACCOUNTS_FILE]).returncode == 0
    subprocess.run(['git', '-C', str(p), 'add', '-A'], check=True)
    staged = subprocess.run(['git', '-C', str(p), 'diff', '--cached', '--name-only'], capture_output=True, text=True).stdout
    assert 'accounts.json' not in staged and '.gitignore' in staged


@pytest.mark.parametrize('ignore', [None, '', '.auto/run/*\n!.auto/run/accounts.json\n'])
def test_not_ignored_provisions_nothing(tmp_path, ignore):
    p = _repo(tmp_path / 'p', ignore)
    with pytest.raises(browser.ProvisionError, match='git-ignored'):
        browser.provision(p, ['admin'], LOCAL)
    assert not (p / '.auto').exists()


@pytest.mark.parametrize('url', ['https://example.com', 'http://user@localhost', None, 'ftp://localhost'])
def test_refused_base_url_provisions_nothing(tmp_path, url):
    p = _repo(tmp_path / 'p')
    ran = []
    cfg = {'enabled': True, 'base_url': url, 'accounts': {'method': 'seed_command', 'seed_command': 'seed'}}
    with pytest.raises(browser.ProvisionError, match='acceptance.browser.base_url'):
        browser.provision(p, ['admin'], cfg, runner=lambda *a: ran.append(a) or (0, ''))
    assert not ran and not (p / '.auto').exists()


def test_bad_or_missing_roles_provision_nothing(tmp_path):
    p = _repo(tmp_path / 'p')
    for roles in ([], ['ad min'], ['1st'], ['x' * 40]):
        with pytest.raises(browser.ProvisionError, match='no accounts made'):
            browser.provision(p, roles, LOCAL)
    assert not (p / '.auto').exists()


def test_seed_command_gets_the_environment_through_the_runner(tmp_path, capfd):
    p = _repo(tmp_path / 'p')
    seen = []

    def runner(command, cwd, env):
        seen.append((command, Path(cwd), dict(env)))
        return 0, 'seeded ' + env['AUTO_TEST_ADMIN_EMAIL']
    cfg = {**LOCAL, 'accounts': {'method': 'seed_command', 'seed_command': 'python seed.py'}}
    got = browser.provision(p, ['admin', 'viewer'], cfg, runner=runner)
    assert got['seeded'] and got['method'] == 'seed_command'
    assert len(seen) == 1 and seen[0][0] == 'python seed.py' and seen[0][1] == p and seen[0][2] == got['env']
    captured = capfd.readouterr()
    assert not any(v in captured.out + captured.err for v in got['env'].values())


def test_failing_seed_command_error_is_redacted(tmp_path):
    p = _repo(tmp_path / 'p')
    cfg = {**LOCAL, 'accounts': {'method': 'seed_command', 'seed_command': 'seed'}}
    with pytest.raises(browser.ProvisionError) as e:
        browser.provision(p, ['admin'], cfg, runner=lambda c, d, env: (3, 'cannot create ' + env['AUTO_TEST_ADMIN_PASSWORD']))
    secrets = browser.known_secrets(p)
    assert 'exited with 3' in str(e.value) and browser.REDACTED in str(e.value)
    assert secrets and not any(v in str(e.value) for v in secrets.values())
    with pytest.raises(browser.ProvisionError, match='seed_command'):
        browser.provision(_repo(tmp_path / 'q'), ['admin'], {**LOCAL, 'accounts': {'method': 'seed_command'}})
    assert not (tmp_path / 'q' / '.auto').exists()


def test_real_seed_runner_passes_the_environment(tmp_path):
    p = _repo(tmp_path / 'p')
    out = tmp_path / 'seen.txt'
    cmd = (f'"{sys.executable}" -c "import os; open(r\'{out}\', \'w\').write('
           f'str(sorted(k for k in os.environ if k.startswith(\'AUTO_TEST_\'))))"')
    browser.provision(p, ['admin'], {**LOCAL, 'accounts': {'method': 'seed_command', 'seed_command': cmd}})
    assert out.read_text() == str(['AUTO_TEST_ADMIN_EMAIL', 'AUTO_TEST_ADMIN_PASSWORD'])


def test_redact_removes_every_value():
    env = browser.account_env(browser.make_accounts(['admin', 'viewer']))
    text = 'login ' + ' and '.join(env.values()) + ' end ' + env['AUTO_TEST_ADMIN_PASSWORD']
    clean = browser.redact(text, env)
    assert not any(v in clean for v in env.values()) and clean.startswith('login ') and clean.endswith(' end ' + browser.REDACTED)
    assert browser.redact(text, list(env.values())) == clean
    assert browser.redact('nothing here', env) == 'nothing here' and browser.redact(None, env) is None
    assert browser.redact('abc', ['', None]) == 'abc'
    detail = {'reasons': ['bad ' + env['AUTO_TEST_VIEWER_EMAIL']], env['AUTO_TEST_ADMIN_EMAIL']: 1, 'exit': 2}
    red = browser.redact_obj(detail, env)
    assert not any(v in json.dumps(red) for v in env.values()) and red['exit'] == 2
    assert browser.redact('ab abc', ['ab', 'abc']) == f'{browser.REDACTED} {browser.REDACTED}'      # longest first


def test_scan_diff_catches_a_leak_and_passes_a_clean_diff():
    env = browser.account_env(browser.make_accounts(['admin']))
    pw = env['AUTO_TEST_ADMIN_PASSWORD']
    leak = ('diff --git a/tests/e2e.py b/tests/e2e.py\n--- a/tests/e2e.py\n+++ b/tests/e2e.py\n@@ -1 +1,2 @@\n'
            f' import os\n+PASSWORD = "{pw}"\n')
    problems = browser.scan_diff(leak, env)
    assert len(problems) == 1 and 'AUTO_TEST_ADMIN_PASSWORD' in problems[0] and 'tests/e2e.py' in problems[0]
    assert pw not in problems[0]
    assert browser.scan_diff(leak, [pw]) and pw not in browser.scan_diff(leak, [pw])[0]
    clean = '+++ b/tests/e2e.py\n+PASSWORD = os.environ["AUTO_TEST_ADMIN_PASSWORD"]\n'
    assert browser.scan_diff(clean, env) == []
    assert browser.scan_diff(f'--- a/x\n+++ b/x\n-old {pw}\n', env) == []          # a removed line is not committed content
    assert browser.scan_diff('', env) == [] and browser.scan_diff(leak, {}) == []


def test_ledger_redacts_event_details(tmp_path):
    env = browser.account_env(browser.make_accounts(['admin']))
    led = events.Ledger(tmp_path, 'r', 'p', redact=lambda d: browser.redact_obj(d, env))
    led.emit('blocker', 'A1', {'action': 'rejected', 'file': 'x.json', 'reasons': ['saw ' + env['AUTO_TEST_ADMIN_PASSWORD']]})
    raw = (tmp_path / events.FILE).read_text(encoding='utf-8')
    assert env['AUTO_TEST_ADMIN_PASSWORD'] not in raw and browser.REDACTED in raw
    plain = events.Ledger(tmp_path / 'b', 'r', 'p')
    assert plain.emit('tests', 'A1', {'result': 'pass', 'exit': 0})['detail'] == {'result': 'pass', 'exit': 0}


def _write_accounts(p):
    env = browser.account_env(browser.make_accounts(['admin']))
    (p / '.auto' / 'run').mkdir(parents=True, exist_ok=True)
    accounts = {'admin': {'email': env['AUTO_TEST_ADMIN_EMAIL'], 'password': env['AUTO_TEST_ADMIN_PASSWORD']}}
    (p / browser.ACCOUNTS_FILE).write_text(json.dumps({'accounts': accounts}), encoding='utf-8')
    return env


def test_runner_never_writes_a_generated_value(project, capfd):
    env = _write_accounts(project)
    pw, email = env['AUTO_TEST_ADMIN_PASSWORD'], env['AUTO_TEST_ADMIN_EMAIL']
    inner = tr.fake_agent(output=f'signed in as {email} with {pw}')
    code = tr.ar.run(project, max_iterations=1, claude=inner, tests=lambda c, d: (0, f'1 passed ({pw})'))
    assert code == 0 and tr.runs(project)[0]['result'] == 'done'
    captured = capfd.readouterr()
    written = _all_text(project / '.auto') + captured.out + captured.err
    assert browser.REDACTED in _all_text(project / '.auto' / 'logs')
    assert pw not in written and email not in written


def test_runner_stops_when_a_generated_value_is_committed(project, capfd):
    env = _write_accounts(project)
    pw = env['AUTO_TEST_ADMIN_PASSWORD']
    inner = tr.fake_agent()

    def leaky(cmd, cwd):
        (Path(cwd) / 'e2e.py').write_text(f'PASSWORD = "{pw}"\n', encoding='utf-8')
        tr.sh(cwd, 'add', 'e2e.py')
        return inner(cmd, cwd)
    calls = []
    code = tr.ar.run(project, max_iterations=3, claude=lambda c, d: calls.append(c) or leaky(c, d))
    assert code == 1 and len(calls) == 1
    assert tr.runs(project)[0]['result'] == 'secret_leak'
    out = capfd.readouterr().out
    assert 'AUTO_TEST_ADMIN_PASSWORD' in out and 'e2e.py' in out and pw not in out
    ends = [e for e in events.read(project / '.auto') if e['type'] == 'run_end']
    assert ends[-1]['detail']['reason'] == 'secret_leak'
    assert pw not in _all_text(project / '.auto')
