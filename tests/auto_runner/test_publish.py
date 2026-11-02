"""auto-runner plugin: `publish` - after a clean run the runner (never the agent) pushes its own run branch and may open a pull
request. The remote is a local bare repository and `gh` is a fake: nothing reaches the network."""
import json
import subprocess

import pytest

from tests.auto_runner.test_runner import ar, picking_agent, project, sh  # noqa: F401  (project is a fixture)

safe = ar.autosafe


def bare_origin(p, tmp_path, name='origin'):
    remote = tmp_path / f'{name}.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True, capture_output=True)
    sh(p, 'remote', 'add', name, str(remote))
    return remote


def remote_branches(remote):
    out = subprocess.run(['git', '-C', str(remote), 'for-each-ref', '--format=%(refname:short)', 'refs/heads'],
                         capture_output=True, text=True, check=True).stdout
    return set(out.split())


def configure(p, **cfg):
    data = json.loads((p / '.claude' / 'auto.json').read_text(encoding='utf-8'))
    data.update(cfg)
    (p / '.claude' / 'auto.json').write_text(json.dumps(data), encoding='utf-8')
    sh(p, 'add', '.claude/auto.json')
    sh(p, 'commit', '-q', '-m', 'cfg')


def events_of(p, kind):
    lines = (p / '.auto' / 'events.jsonl').read_text(encoding='utf-8').splitlines()
    return [e for e in map(json.loads, lines) if e['type'] == kind]


class FakeGh:
    def __init__(self, code=0, output='https://github.com/example/proj/pull/7\n'):
        self.calls, self.code, self.output = [], code, output

    def __call__(self, argv, cwd):
        self.calls.append(argv)
        return self.code, self.output


# ---------- a run ----------

def test_publishing_is_off_by_default(project, tmp_path):
    remote = bare_origin(project, tmp_path)
    assert ar.run(project, max_iterations=3, claude=picking_agent()) == 0
    assert remote_branches(remote) == set() and events_of(project, 'publish') == []


def test_a_clean_run_pushes_only_its_own_branch(project, tmp_path, capsys):
    remote = bare_origin(project, tmp_path)
    configure(project, publish={'push': True})
    assert ar.run(project, max_iterations=3, claude=picking_agent()) == 0
    [branch] = remote_branches(remote)
    assert branch.startswith('auto/')                                     # never main
    [event] = events_of(project, 'publish')
    assert event['item'] is None and event['detail']['pushed'] is True and event['detail']['commits'] == 2
    assert 'pull_request' not in event['detail'] and f'pushed {branch} to origin' in capsys.readouterr().out
    head = subprocess.run(['git', '-C', str(project), 'rev-parse', branch], capture_output=True, text=True).stdout
    assert subprocess.run(['git', '-C', str(remote), 'rev-parse', branch], capture_output=True, text=True).stdout == head


def test_a_pull_request_is_opened_with_gh(project, tmp_path):
    bare_origin(project, tmp_path)
    configure(project, publish={'push': True, 'pull_request': True})
    gh = FakeGh()
    assert ar.run(project, max_iterations=3, claude=picking_agent(), pr_runner=gh) == 0
    [argv] = gh.calls
    assert argv[:3] == ['gh', 'pr', 'create'] and argv[argv.index('--base') + 1] == 'main'
    assert argv[argv.index('--head') + 1].startswith('auto/')
    assert 'Read the diff before merging' in argv[argv.index('--body') + 1]
    assert events_of(project, 'publish')[0]['detail']['pr_url'] == 'https://github.com/example/proj/pull/7'


def test_a_failed_pull_request_is_reported_not_fatal(project, tmp_path):
    remote = bare_origin(project, tmp_path)
    configure(project, publish={'push': True, 'pull_request': True})
    assert ar.run(project, max_iterations=3, claude=picking_agent(), pr_runner=FakeGh(127, 'gh is not installed')) == 0
    detail = events_of(project, 'publish')[0]['detail']
    assert detail['pushed'] is True and 'exit 127' in detail['pr_error'] and len(remote_branches(remote)) == 1


def test_a_red_run_publishes_nothing(project, tmp_path):
    remote = bare_origin(project, tmp_path)
    configure(project, publish={'push': True, 'pull_request': True}, test_command='python -c "import sys; sys.exit(3)"')
    gh = FakeGh()
    assert ar.run(project, max_iterations=3, claude=picking_agent(), pr_runner=gh) == 1
    assert remote_branches(remote) == set() and gh.calls == [] and events_of(project, 'publish') == []


def test_a_rejected_push_is_reported(project, tmp_path):
    remote = bare_origin(project, tmp_path)
    configure(project, publish={'push': True})
    # the remote refuses every push
    hook = remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\necho "denied by policy" >&2\nexit 1\n', encoding='utf-8')
    hook.chmod(0o755)
    assert ar.run(project, max_iterations=3, claude=picking_agent()) == 0
    detail = events_of(project, 'publish')[0]['detail']
    assert detail['pushed'] is False and 'git push failed' in detail['error']


def test_publish_refuses_a_branch_that_is_not_a_run_branch(project):
    cfg = {**ar.autocfg.load(project), 'publish': {'push': True}, 'lanes': {'protected': ['auto/keep-*']}}
    for branch in ('main', 'feature/x', 'auto/keep-1'):
        out = ar.publish(project, project, cfg, 'main', branch, FakeGh())
        assert out['pushed'] is False and out['error'] == 'refused: not a run branch'


# ---------- preflight ----------

def test_preflight_needs_the_remote(project):
    cfg = {**ar.autocfg.load(project), 'publish': {'push': True}}
    [problem] = safe.publish_problems(project, cfg, 'main')
    assert 'no remote "origin"' in problem


def test_preflight_takes_another_remote_name(project, tmp_path):
    bare_origin(project, tmp_path, name='upstream')
    cfg = {**ar.autocfg.load(project), 'publish': {'push': True, 'remote': 'upstream'}}
    assert safe.publish_problems(project, cfg, 'main') == []


@pytest.mark.parametrize('publish,extra,needle', [
    ({'pull_request': True}, {}, 'needs "publish.push"'),
    ({'push': True}, {'branch_prefix': ''}, 'needs a "branch_prefix"'),
    ({'push': True}, {'lanes': {'protected': ['auto/*']}}, 'lanes.protected'),
    ({'push': True}, {'branch_prefix': 'ma'}, 'must not be the base branch'),
    ({'push': True, 'remote': '--upload-pack=x'}, {}, 'remote name'),
])
def test_preflight_refuses_unsafe_settings(project, tmp_path, publish, extra, needle):
    bare_origin(project, tmp_path)
    cfg = {**ar.autocfg.load(project), 'publish': publish, **extra}
    problems = safe.publish_problems(project, cfg, 'main')
    assert problems and needle in problems[0]


def test_the_agent_still_may_not_push():
    assert 'Bash(git push:*)' in safe.required_rules('main', env_read=True)
