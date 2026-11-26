"""`autosupervisor.py watch` (decision record 0012, Phase 5): passes over several projects until STOP. No network."""
import importlib.util
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parents[2] / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autosupervisor_watch_t', BIN / 'autosupervisor.py')
sup = importlib.util.module_from_spec(spec)
sys.modules['autosupervisor_watch_t'] = sup
spec.loader.exec_module(sup)

from tests.auto_runner.fake_forge import FakeForge  # noqa: E402

ENV = {'AUTO_SUPERVISOR_URL': 'https://central.test', 'AUTO_SUPERVISOR_TOKEN': 'secret-token-123',
       'AUTO_SUPERVISOR_MERGER': 'sup-bot'}


def fake_http(calls, role='supervisor'):
    def http(method, path, body=None):
        calls.append((method, path))
        if path == sup.WHOAMI:
            return 200, {'role': role}
        if path.startswith('/api/v1/supervisor/state'):
            return 200, {'tasks': [], 'runners': []}
        return 200, {}
    return http


def test_github_repo_parses_https_and_ssh():
    assert sup.github_repo('https://github.com/acme/web.git\n') == 'acme/web'
    assert sup.github_repo('git@github.com:acme/web.git') == 'acme/web'
    assert sup.github_repo('https://gitlab.com/acme/web') is None and sup.github_repo('') is None


def test_watch_projects_from_the_list_or_the_single_settings(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir(), b.mkdir()
    env = {'AUTO_SUPERVISOR_PROJECTS': f'erp={a}, plugins={b}, bad, gone={tmp_path / "nope"}'}
    projects, problems = sup.watch_projects(env, origin=lambda d: 'acme/' + Path(d).name)
    assert [(p['slug'], p['repo']) for p in projects] == [('erp', 'acme/a'), ('plugins', 'acme/b')]
    assert len(problems) == 2
    single, none = sup.watch_projects({'AUTO_SUPERVISOR_PROJECT': 'x', 'AUTO_SUPERVISOR_REPO': 'o/r'})
    assert single == [{'slug': 'x', 'repo_dir': '.', 'repo': 'o/r'}] and none == []
    assert sup.watch_projects({})[0] == []


def test_watch_runs_each_project_with_its_own_clone_then_stops(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir(), b.mkdir()
    env = dict(ENV, AUTO_SUPERVISOR_PROJECTS=f'erp={a},plugins={b}')
    calls, lines, reads, forges, t = [], [], [], [], [0]

    def make_read(repo_dir):
        reads.append(repo_dir)
        return lambda ref, path: None
    code = sup.watch_main([], env, http=fake_http(calls), make_read=make_read,
                          make_forge=lambda repo: forges.append(repo) or FakeForge(),
                          make_deployer=lambda d: object(), out=lines.append, sleep=lambda s: t.__setitem__(0, t[0] + s),
                          clock=lambda: t[0], origin=lambda d: 'acme/' + Path(d).name, max_passes=2,
                          stop=tmp_path / 'STOP')
    assert code == 0 and reads == [str(a), str(b)] * 2 and forges == ['acme/a', 'acme/b'] * 2
    states = [p for m, p in calls if p.startswith('/api/v1/supervisor/state')]
    assert any('project=erp' in p for p in states) and any('project=plugins' in p for p in states)
    assert any(l.startswith('[erp] ') for l in lines) and any(l.startswith('[plugins] ') for l in lines)
    assert 'secret-token-123' not in '\n'.join(lines)


def test_one_projects_error_does_not_stop_the_watch_and_stop_file_ends_it(tmp_path):
    a = tmp_path / 'a'
    a.mkdir()
    stop = tmp_path / 'STOP'
    lines, t = [], [0]

    def boom(repo_dir):
        raise RuntimeError('clone is broken')

    def sleep(s):
        t[0] += s
        stop.write_text('')
    code = sup.watch_main(['--every', '60'], dict(ENV, AUTO_SUPERVISOR_PROJECTS=f'erp={a}'), http=fake_http([]),
                          make_read=boom, make_forge=lambda r: FakeForge(), make_deployer=lambda d: object(),
                          out=lines.append, sleep=sleep, clock=lambda: t[0], origin=lambda d: 'acme/a', stop=stop)
    assert code == 0 and '[erp] error: RuntimeError: clone is broken' in lines
    assert lines[-1] == 'Supervisor: STOP file found: stopping.'


def test_watch_refuses_without_settings_or_a_supervisor_token(tmp_path):
    lines = []
    assert sup.watch_main([], {}, out=lines.append) == sup.EXIT_CONFIG and 'not configured' in lines[0]
    env = dict(ENV, AUTO_SUPERVISOR_PROJECT='x', AUTO_SUPERVISOR_REPO='o/r')
    assert sup.watch_main([], env, http=fake_http([], role='developer'), out=lines.append) == sup.EXIT_REFUSED


def test_main_dispatches_watch(tmp_path):
    lines = []
    assert sup.main(['watch'], env={}, out=lines.append) == sup.EXIT_CONFIG
