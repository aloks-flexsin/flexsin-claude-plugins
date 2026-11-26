"""The Supervisor's deploy step (bin/autodeploy.py, decision record 0012 Phase 5) with fakes: no network, no shell."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parents[2] / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autodeploy_t', BIN / 'autodeploy.py')
dep = importlib.util.module_from_spec(spec)
sys.modules['autodeploy_t'] = dep
spec.loader.exec_module(dep)

SHA = 'a' * 40


def reader(deploy=None, raw=None):
    def read(ref, path):
        if path != '.claude/auto.json':
            return None
        if raw is not None:
            return raw, 'x'
        return (json.dumps({'deploy': deploy}) if deploy is not None else '{}'), 'x'
    return read


class FakeDeployer:
    def __init__(self, codes=(0,), sha=SHA, output='built ok'):
        self.codes, self.sha, self.output, self.ran = list(codes), sha, output, []

    def head(self, base_ref):
        return self.sha

    def run(self, command, sha, timeout):
        self.ran.append((command, sha, timeout))
        return self.codes.pop(0), self.output


def fake_http(calls, add_status=201):
    def http(method, path, body=None):
        calls.append((method, path, body))
        if '/tasks?' in path:
            return add_status, {'task': {'number': 41}} if add_status == 201 else {'error': 'no'}
        return 200, {'last_deploy': None}
    return http


def step(deploy, deployer, state=None, mode='auto', probe=lambda url: 200, add_status=201, env=None):
    calls = []
    out = dep.deploy_step('demo', fake_http(calls, add_status), reader(deploy), 'origin/main', state or {}, mode,
                          deployer, probe=probe, sleep=lambda s: None, env=env or {})
    return out, calls


def phases(calls):
    return [c[2]['phase'] for c in calls if '/deploys' in c[1]]


def test_no_command_or_manual_mode_deploys_nothing():
    d = FakeDeployer()
    assert step(None, d) == ([], []) and step({'smoke_url': 'https://x'}, d) == ([], [])
    assert step({'command': 'make deploy'}, d, mode='manual') == ([], []) and d.ran == []


def test_a_green_deploy_is_recorded_started_then_done():
    d = FakeDeployer()
    out, calls = step({'command': 'make deploy', 'smoke_url': 'https://app.test/health', 'timeout_s': 30}, d)
    assert out == [{'sha': SHA, 'outcome': 'deployed', 'reasons': [], 'task': None}]
    assert phases(calls) == ['started', 'done'] and d.ran == [('make deploy', SHA, 30)]


def test_an_already_deployed_head_is_not_deployed_again_but_a_started_one_is():
    d = FakeDeployer(codes=(0, 0))
    for phase in ('done', 'failed', 'rolled_back'):
        assert step({'command': 'x'}, d, state={'last_deploy': {'sha': SHA, 'phase': phase}}) == ([], [])
    out, _ = step({'command': 'x'}, d, state={'last_deploy': {'sha': SHA, 'phase': 'started'}})
    assert out[0]['outcome'] == 'deployed'
    out, _ = step({'command': 'x'}, d, state={'last_deploy': {'sha': 'b' * 40, 'phase': 'done'}})
    assert out[0]['outcome'] == 'deployed'


def test_a_failed_smoke_check_adds_a_fix_task_and_rolls_back():
    d = FakeDeployer(codes=(0, 0))
    seen = []
    out, calls = step({'command': 'deploy', 'smoke_url': 'https://app.test', 'rollback_command': 'undo', 'smoke_tries': 2},
                      d, probe=lambda url: seen.append(url) or 503)
    assert len(seen) == 2
    [result] = out
    assert result['outcome'] == 'failed' and result['task'] == 41
    assert 'answered 503 after 2 tries' in result['reasons'][0] and 'rolled back' in result['reasons']
    assert phases(calls) == ['started', 'failed', 'rolled_back']
    [add] = [c[2] for c in calls if '/tasks?' in c[1]]
    assert add['title'] == 'Fix the failed deploy of ' + SHA[:10] and 'failed deploy of ' + SHA in add['from']
    assert 'assignee' not in add                                       # the pool takes it
    failed = [c[2] for c in calls if '/deploys' in c[1] and c[2]['phase'] == 'failed'][0]
    assert failed['task'] == 41 and d.ran[1] == ('undo', SHA, 600)


def test_a_failing_command_is_not_smoke_checked_and_a_timeout_says_so():
    probes = []
    out, _ = step({'command': 'deploy', 'smoke_url': 'https://x'}, FakeDeployer(codes=(2,)),
                  probe=lambda u: probes.append(u) or 200)
    assert out[0]['reasons'][0] == 'the deploy command exited 2' and probes == []
    out, _ = step({'command': 'deploy'}, FakeDeployer(codes=(None,)))
    assert out[0]['reasons'][0] == 'the deploy command timed out'


def test_secrets_in_the_output_are_redacted_and_cut():
    d = FakeDeployer(codes=(1,), output='x' * 5000 + ' token=supersecret123 ')
    _, calls = step({'command': 'deploy'}, d, env={'VERCEL_TOKEN': 'supersecret123', 'HOME': '/root'})
    sent = json.dumps([c[2] for c in calls])
    assert 'supersecret123' not in sent and '***' in sent
    failed = [c[2] for c in calls if '/deploys' in c[1] and c[2]['phase'] == 'failed'][0]
    assert len(failed['output']) <= dep.TAIL


def test_a_bad_config_is_an_error_and_deploys_nothing():
    d = FakeDeployer()
    for deploy in ({'command': 3}, {'smoke_tries': -1, 'command': 'x'}, {'what': 1}):
        out, calls = step(deploy, d)
        assert out[0]['outcome'] == 'error' and calls == [] and d.ran == []
    out = dep.deploy_step('demo', fake_http([]), reader(raw='{nope'), 'origin/main', {}, 'auto', d)
    assert 'not valid JSON' in out[0]['reasons'][0]


def test_an_unrecorded_start_runs_nothing():
    d = FakeDeployer()
    out = dep.deploy_step('demo', lambda m, p, b=None: (500, {}), reader({'command': 'x'}), 'origin/main', {}, 'auto', d)
    assert out[0]['outcome'] == 'error' and d.ran == []


def test_smoke_needs_a_2xx():
    assert dep.smoke('', 3, None, None) == (True, None)
    answers = iter([0, 302, 204])
    assert dep.smoke('https://x', 3, lambda u: next(answers), lambda s: None) == (True, None)


def test_shell_deployer_uses_its_own_worktree(tmp_path):
    repo = tmp_path / 'proj'
    repo.mkdir()
    calls = []

    def runner(cmd, **kw):
        calls.append((cmd, kw.get('cwd')))
        out = SHA + '\n' if cmd[-2:] == ['rev-parse', 'origin/main'] else 'ok'
        return subprocess.CompletedProcess(cmd, 0, out, '')
    d = dep.ShellDeployer(repo, runner=runner)
    assert d.head('origin/main') == SHA
    assert d.run('make deploy', SHA, 60) == (0, 'ok')
    wt = str(tmp_path / 'proj-deploy')
    assert ['git', '-C', str(repo), 'worktree', 'add', '-q', '--detach', '--force', wt, SHA] in [c[0] for c in calls]
    assert calls[-1] == ('make deploy', wt)
