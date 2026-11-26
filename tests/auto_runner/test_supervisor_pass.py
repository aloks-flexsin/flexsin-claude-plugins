"""The Supervisor entry point (B8): whoami check first, exit codes, no token in output. No network."""
import importlib.util
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parents[2] / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autosupervisor_entry', BIN / 'autosupervisor.py')
sup = importlib.util.module_from_spec(spec)
sys.modules['autosupervisor_entry'] = sup
spec.loader.exec_module(sup)

from tests.auto_runner.fake_forge import FakeForge  # noqa: E402

ENV = {'AUTO_SUPERVISOR_URL': 'https://central.test', 'AUTO_SUPERVISOR_TOKEN': 'secret-token-123',
       'AUTO_SUPERVISOR_PROJECT': 'demo', 'AUTO_SUPERVISOR_REPO': 'o/r', 'AUTO_SUPERVISOR_MERGER': 'sup-bot'}


def fake_http(role, calls, status=200):
    def http(method, path, body=None):
        calls.append((method, path))
        if path == sup.WHOAMI:
            return status, {'role': role}
        if path.startswith('/api/v1/supervisor/state'):
            return 200, {'tasks': []}
        return 404, {}
    return http


def run(env, role='supervisor', status=200):
    calls, lines = [], []
    code = sup.main(env=env, http=fake_http(role, calls, status), read=lambda ref, path: None, forge=FakeForge(),
                    out=lines.append)
    return code, calls, lines


def test_command_refuses_a_project_token_and_a_member_below_developer():
    code, calls, lines = run(ENV, role=None)                 # a project token has no account
    assert code == sup.EXIT_REFUSED and calls == [('GET', sup.WHOAMI)] and 'sign this computer in' in lines[0]
    for role in ('user', 'admin'):                           # a member acts as the Supervise stage (decision 0012, Phase 8)
        calls, lines = [], []

        def http(method, path, body=None):
            calls.append((method, path))
            return (200, {'role': role}) if path == sup.WHOAMI else (403, {'error': 'no'})
        code = sup.main(env=ENV, http=http, read=lambda ref, path: None, forge=FakeForge(), out=lines.append)
        assert code == sup.EXIT_REFUSED and 'developer or above in: demo' in lines[0]
        assert [p for _m, p in calls] == [sup.WHOAMI, sup.STATE % 'demo']       # no decision was attempted
        assert 'secret-token-123' not in ' '.join(lines)


def test_a_member_runs_a_pass_as_the_supervise_stage():
    code, calls, lines = run(ENV, role='user')
    assert code == sup.EXIT_OK and lines == ['Nothing to do: no plan waits and no pull request is ready.']


def test_service_down_or_token_rejected_refuses():
    code, calls, lines = run(ENV, status=401)
    assert code == sup.EXIT_REFUSED and 'did not answer' in lines[0] and len(calls) == 1


def test_missing_configuration_exits_2_before_any_call(tmp_path):
    env = {k: v for k, v in ENV.items() if k != 'AUTO_SUPERVISOR_PROJECT'}
    env['AUTO_SUPERVISOR_REPO_DIR'] = str(tmp_path)          # no .claude/auto.json there to take the project from
    code, calls, lines = run(env)
    assert code == sup.EXIT_CONFIG and calls == [] and 'the project' in lines[0] and '/auto-runner:setup' in lines[0]


def test_settings_come_from_the_project_config_and_the_sign_in(tmp_path, monkeypatch):
    import json
    (tmp_path / '.claude').mkdir()
    (tmp_path / '.claude' / 'auto.json').write_text(json.dumps(
        {'test_command': 'x', 'central': {'enabled': True, 'url': 'https://central.test', 'project': 'acme-web'}}),
        encoding='utf-8')
    safe = sup._load('autorunner_safe', 'autosafe.py')
    monkeypatch.setattr(safe, 'central_token', lambda cfg, env=None: (safe.PERSONAL_TOKEN_PREFIX + 'mine', 'sign-in'))
    no_gh = lambda *a, **k: (_ for _ in ()).throw(OSError('no gh'))     # noqa: E731
    s, missing = sup.resolve_settings({'AUTO_SUPERVISOR_REPO_DIR': str(tmp_path)}, origin=lambda d: 'acme/web',
                                      run=no_gh)
    assert missing == [] and (s['url'], s['project'], s['repo']) == ('https://central.test', 'acme-web', 'acme/web')
    assert s['token'].endswith('mine') and s['github_token'] == '' and s['merger'] == ''
    s, _ = sup.resolve_settings({'AUTO_SUPERVISOR_REPO_DIR': str(tmp_path), 'AUTO_SUPERVISOR_PROJECT': 'other',
                                 'GH_TOKEN': 'g'}, origin=lambda d: 'acme/web', run=no_gh)
    assert s['project'] == 'other' and s['github_token'] == 'g'          # an override wins


def test_supervisor_runs_a_quiet_pass_and_exits_0():
    code, calls, lines = run(ENV)
    assert code == sup.EXIT_OK
    assert calls[0] == ('GET', sup.WHOAMI) and any('state' in p for _m, p in calls)
    assert lines == ['Nothing to do: no plan waits and no pull request is ready.']


def test_a_step_error_exits_1():
    def http(method, path, body=None):
        return (200, {'role': 'supervisor'}) if path == sup.WHOAMI else (500, {})
    lines = []
    code = sup.main(env=ENV, http=http, read=lambda r, p: None, forge=FakeForge(), out=lines.append)
    assert code == sup.EXIT_ERRORS and any('could not read the state' in line for line in lines)


def test_git_reader_reads_the_base_ref_not_the_working_tree(tmp_path):
    import subprocess
    def g(*a):
        subprocess.run(['git', '-C', str(tmp_path), *a], check=True, capture_output=True)
    g('init', '-q', '-b', 'main')
    g('config', 'user.email', 't@t'); g('config', 'user.name', 't')
    (tmp_path / 'f.txt').write_text('committed')
    g('add', '.'); g('commit', '-qm', 'x')
    (tmp_path / 'f.txt').write_text('edited')
    text, sha = sup.git_reader(tmp_path)('main', 'f.txt')
    assert text == 'committed' and len(sha) == 40
    assert sup.git_reader(tmp_path)('main', 'missing') is None


def test_command_file_and_readme_mention_each_other():
    root = Path(__file__).resolve().parents[2] / 'auto-runner'
    assert 'autosupervisor.py' in (root / 'commands' / 'supervisor.md').read_text()
    readme = (root / 'README.md').read_text()
    assert '/auto-runner:supervisor' in readme and 'AUTO_SUPERVISOR_URL' in readme


# ---------- `add`: the supervisor adds a task (decision record 0010, 6a) ----------

def add_http(calls, status=201, body=None):
    def http(method, path, sent=None):
        calls.append((method, path, sent))
        return status, body if body is not None else {'task': {'number': 7, 'status': 'open'}}
    return http


def test_add_posts_the_task_with_its_source_and_assignee():
    calls, lines = [], []
    code = sup.main(['add', '--title', 'Test less', '--from', "the user's request", '--for', 'dev@example.com'],
                    env=ENV, http=add_http(calls), out=lines.append)
    assert code == 0 and lines == ['Task 7: added for dev@example.com (open).']
    method, path, sent = calls[0]
    assert method == 'POST' and path == '/api/v1/supervisor/tasks?project=demo'
    assert sent == {'title': 'Test less', 'from': "the user's request", 'assignee': 'dev@example.com', 'description': ''}
    assert len(calls) == 1                                       # no whoami, no pass, no merge


def test_add_sends_a_plan_and_says_a_person_approves_it(tmp_path):
    (tmp_path / 'p.md').write_text('## Intent\nx', encoding='utf-8')
    (tmp_path / 'i.md').write_text('- [ ] **T1 x.** y', encoding='utf-8')
    calls, lines = [], []
    code = sup.main(['add', '--title', 'T', '--from', 'S', '--for', 'dev@example.com', '--project', 'alpha',
                     '--plan', str(tmp_path / 'p.md'), '--items', str(tmp_path / 'i.md')],
                    env=ENV, http=add_http(calls, body={'task': {'number': 8, 'status': 'plan_review'}}), out=lines.append)
    assert code == 0 and 'waits for a person to approve it' in lines[0]
    assert calls[0][1] == '/api/v1/supervisor/tasks?project=alpha'
    assert calls[0][2]['plan'] == '## Intent\nx' and calls[0][2]['items'] == '- [ ] **T1 x.** y'


def test_add_reports_refusals_and_bad_input(tmp_path):
    lines = []
    assert sup.main(['add', '--title', 'T', '--from', 'S', '--for', 'x@y', '--plan', str(tmp_path / 'p.md')],
                    env=ENV, http=add_http([]), out=lines.append) == 2               # a plan needs its items
    assert sup.main(['add', '--title', 'T', '--from', 'S', '--for', 'x@y', '--plan', 'missing.md', '--items', 'missing.md'],
                    env=ENV, http=add_http([]), out=lines.append) == 2               # unreadable file
    env = {k: v for k, v in ENV.items() if k != 'AUTO_SUPERVISOR_PROJECT'}
    assert sup.main(['add', '--title', 'T', '--from', 'S', '--for', 'x@y'], env=env, http=add_http([]),
                    out=lines.append) == 2
    lines.clear()
    code = sup.main(['add', '--title', 'T', '--from', 'S', '--for', 'viewer@example.com'], env=ENV,
                    http=add_http([], 400, {'error': 'viewer@example.com is not a developer or above'}), out=lines.append)
    assert code == 1 and lines == ['Not done: viewer@example.com is not a developer or above']
    assert sup.main(['add', '--title', 'T', '--from', 'S', '--for', 'x@y'], env=ENV, http=add_http([], 0, {}),
                    out=lines.append) == 3
    assert all('secret-token-123' not in line for line in lines)
