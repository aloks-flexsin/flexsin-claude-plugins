"""auto-runner plugin: `publish` - after a clean run the runner (never the agent) pushes its own run branch
(`<user>/<account>-claude-auto-runner/<type>/<brief>`) and opens a pull request into the base; both are on unless turned off. The remote is a
local bare repository and `gh` is a fake: nothing reaches the network."""
import json
import subprocess

import pytest

from tests.auto_runner.test_runner import ar, picking_agent, project, sh  # noqa: F401  (project is a fixture)

safe = ar.autosafe


def bare_origin(p, tmp_path, name='origin'):
    """A local bare remote holding the project's main, as a shared repository would."""
    remote = tmp_path / f'{name}.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True, capture_output=True)
    sh(p, 'remote', 'add', name, str(remote))
    sh(p, 'push', '-q', name, 'main')
    return remote


def remote_branches(remote):
    """The branches on the remote other than main."""
    out = subprocess.run(['git', '-C', str(remote), 'for-each-ref', '--format=%(refname:short)', 'refs/heads'],
                         capture_output=True, text=True, check=True).stdout
    return set(out.split()) - {'main'}


def configure(p, **cfg):
    data = json.loads((p / '.claude' / 'auto.json').read_text(encoding='utf-8'))
    data.update(cfg)
    (p / '.claude' / 'auto.json').write_text(json.dumps(data), encoding='utf-8')
    sh(p, 'add', '.claude/auto.json')
    sh(p, 'commit', '-q', '-m', 'cfg')
    if subprocess.run(['git', '-C', str(p), 'remote', 'get-url', 'origin'], capture_output=True).returncode == 0:
        sh(p, 'push', '-q', 'origin', 'main')                              # the config is on the shared main too


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

def test_publishing_is_on_by_default(project, tmp_path):
    remote = bare_origin(project, tmp_path)
    gh = FakeGh()
    assert ar.run(project, max_iterations=3, claude=picking_agent(), pr_runner=gh) == 0
    [branch] = remote_branches(remote)
    assert branch.startswith('tester/acct-claude-auto-runner/feat/queue-')
    [argv] = gh.calls
    assert argv[argv.index('--head') + 1] == branch and argv[argv.index('--base') + 1] == 'main'
    assert events_of(project, 'publish')[0]['detail']['pushed'] is True


def test_false_turns_publishing_off(project, tmp_path):
    remote = bare_origin(project, tmp_path)
    configure(project, publish={'push': False})
    assert ar.run(project, max_iterations=3, claude=picking_agent()) == 0
    assert remote_branches(remote) == set() and events_of(project, 'publish') == []


def test_no_remote_and_no_setting_means_no_publishing_and_no_refusal(project, capsys):
    assert ar.run(project, max_iterations=3, claude=picking_agent()) == 0
    assert events_of(project, 'publish') == []
    assert 'no remote "origin": starting from the local main; nothing will be published' in capsys.readouterr().out


def test_pull_request_false_pushes_without_a_pull_request(project, tmp_path):
    remote = bare_origin(project, tmp_path)
    configure(project, publish={'pull_request': False})
    assert ar.run(project, max_iterations=3, claude=picking_agent()) == 0          # the real gh would fail the test
    assert len(remote_branches(remote)) == 1 and 'pr_url' not in events_of(project, 'publish')[0]['detail']


def test_a_clean_run_pushes_only_its_own_branch(project, tmp_path, capsys):
    remote = bare_origin(project, tmp_path)
    configure(project, publish={'push': True, 'pull_request': False})
    assert ar.run(project, max_iterations=3, claude=picking_agent()) == 0
    [branch] = remote_branches(remote)
    assert branch.startswith('tester/acct-claude-auto-runner/feat/')                         # this runner's own branch, never main
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
    head = argv[argv.index('--head') + 1]
    assert head.startswith('tester/acct-claude-auto-runner/feat/queue-')
    assert argv[argv.index('--title') + 1] == f"feat: {head.split('/', 3)[3].replace('-', ' ')} (tester via acct-claude-auto-runner)"
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
    configure(project, publish={'push': True, 'pull_request': True}, test_command='python -c "import sys; sys.exit(3)"',
              review={'max_fix_rounds': 0})
    gh = FakeGh()
    assert ar.run(project, max_iterations=3, claude=picking_agent(), pr_runner=gh) == 1
    assert remote_branches(remote) == set() and gh.calls == [] and events_of(project, 'publish') == []


def test_a_rejected_push_is_reported(project, tmp_path):
    remote = bare_origin(project, tmp_path)
    configure(project, publish={'push': True, 'pull_request': False})
    # the remote refuses every push
    hook = remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\necho "denied by policy" >&2\nexit 1\n', encoding='utf-8')
    hook.chmod(0o755)
    assert ar.run(project, max_iterations=3, claude=picking_agent()) == 0
    detail = events_of(project, 'publish')[0]['detail']
    assert detail['pushed'] is False and 'git push failed' in detail['error']


def test_publish_refuses_a_branch_that_is_not_this_runners(project):
    cfg = {**ar.autocfg.load(project), 'publish': {'push': True}, 'lanes': {'protected': ['tester/acct-claude-auto-runner/feat/keep-*']}}
    for branch in ('main', 'feature/x', 'auto/20260101-0000', 'other/acct-claude-auto-runner/feat/x', 'tester/other-claude-auto-runner/feat/x', 'tester/acct-claude-auto-runner/feat/keep-1'):
        out = ar.publish(project, project, cfg, 'main', branch, FakeGh())
        assert out['pushed'] is False and out['error'].startswith(f'refused: {branch} is not this runner')


# ---------- preflight ----------

def test_preflight_needs_the_remote(project):
    cfg = {**ar.autocfg.load(project), 'publish': {'push': True}}
    [problem] = safe.publish_problems(project, cfg, 'main')
    assert 'no remote "origin"' in problem


def test_preflight_takes_another_remote_name(project, tmp_path):
    bare_origin(project, tmp_path, name='upstream')
    cfg = {**ar.autocfg.load(project), 'publish': {'push': True, 'remote': 'upstream'}}
    assert safe.publish_problems(project, cfg, 'main') == []


@pytest.mark.parametrize('publish,extra,base,needle', [
    ({'push': False, 'pull_request': True}, {}, 'main', 'needs "publish.push"'),
    ({'push': True}, {'lanes': {'protected': ['tester/acct-claude-auto-runner/*']}}, 'main', 'lanes.protected'),
    ({'push': True}, {}, 'tester/acct-claude-auto-runner/main', 'must not be the base branch'),
    ({'push': True, 'remote': '--upload-pack=x'}, {}, 'main', 'remote name'),
])
def test_preflight_refuses_unsafe_settings(project, tmp_path, publish, extra, base, needle):
    bare_origin(project, tmp_path)
    cfg = {**ar.autocfg.load(project), 'publish': publish, **extra}
    problems = safe.publish_problems(project, cfg, base)
    assert problems and needle in problems[0]


def test_default_publishing_without_a_remote_is_not_a_problem(project):
    assert safe.publish_problems(project, ar.autocfg.load(project), 'main') == []


def test_the_runner_blocks_push_for_its_agent_and_settings_need_no_push_rule(project):
    seen = []
    agent = picking_agent()

    def claude(cmd, cwd):
        seen.append(cmd)
        return agent(cmd, cwd)
    assert ar.run(project, max_iterations=1, claude=claude) == 0
    cmd = seen[0]
    denied = cmd[cmd.index('--disallowedTools') + 1:cmd.index('--max-budget-usd')]
    assert denied == ['Bash(git push:*)', 'PowerShell(git push:*)']
    assert not any('git push' in r for r in safe.required_rules('main', env_read=True))
    assert 'git push' not in ' '.join(safe.missing_rules({'permissions': {'deny': []}}, 'main'))
    assert 'PowerShell(git push:*)' in ar.autobrowser.EXPLORE_DENIED and 'Bash' in ar.autobrowser.EXPLORE_DENIED


# ---------- REST fallback when GraphQL is blocked ----------

GRAPHQL_403 = 'error connecting to api.github.com\nGraphQL: HTTP 403 Forbidden (https://api.github.com/graphql)\n'
RUNNER = 'tester/acct-claude-auto-runner'
BRANCH = f'{RUNNER}/fix/rest-pr'


class ScriptedGh:
    """A fake `pr_runner` answering each call in turn from `answers` [(code, output), ...]."""
    def __init__(self, *answers):
        self.calls, self.answers = [], list(answers)

    def __call__(self, argv, cwd):
        self.calls.append(argv)
        return self.answers.pop(0)


def rest_ready(p, tmp_path, url='https://github.com/o/r.git'):
    """A run branch with one commit; `origin` reads as `url` but pushes to a local bare repository."""
    remote = bare_origin(p, tmp_path)
    sh(p, 'remote', 'set-url', 'origin', url)
    sh(p, 'remote', 'set-url', '--push', 'origin', str(remote))
    sh(p, 'switch', '-q', '-c', BRANCH)
    (p / 'rest.txt').write_text('x\n', encoding='utf-8')
    sh(p, 'add', 'rest.txt')
    sh(p, 'commit', '-q', '-m', 'rest')
    return {**ar.autocfg.load(p), 'publish': {'push': True, 'pull_request': True}}


def publish_with(p, cfg, gh):
    return ar.publish(p, p, cfg, 'main', BRANCH, gh, name=RUNNER)


def test_a_graphql_block_falls_back_to_the_rest_api(project, tmp_path):
    cfg = rest_ready(project, tmp_path)
    gh = ScriptedGh((1, GRAPHQL_403), (0, 'https://github.com/o/r/pull/9\n'))
    out = publish_with(project, cfg, gh)
    assert out['pushed'] is True and out['pr_url'] == 'https://github.com/o/r/pull/9' and 'pr_error' not in out
    create, rest = gh.calls
    assert create[:3] == ['gh', 'pr', 'create']
    assert rest[:5] == ['gh', 'api', 'repos/o/r/pulls', '-X', 'POST'] and rest[-2:] == ['--jq', '.html_url']
    fields = dict(rest[i + 1].split('=', 1) for i, a in enumerate(rest) if a == '-f')
    assert fields['head'] == BRANCH and fields['base'] == 'main'
    assert fields['title'] == create[create.index('--title') + 1] and fields['body'] == create[create.index('--body') + 1]


def test_a_failure_without_graphql_makes_no_rest_call(project, tmp_path):
    cfg = rest_ready(project, tmp_path)
    gh = ScriptedGh((1, 'a pull request for branch "x" into branch "main" already exists'))
    out = publish_with(project, cfg, gh)
    assert len(gh.calls) == 1 and 'pr_url' not in out
    assert out['pr_error'].startswith('gh pr create failed (exit 1)') and 'gh api' not in out['pr_error']


def test_both_failing_records_both_errors(project, tmp_path):
    cfg = rest_ready(project, tmp_path)
    gh = ScriptedGh((1, GRAPHQL_403), (1, 'gh: Not Found (HTTP 404)'))
    out = publish_with(project, cfg, gh)
    assert len(gh.calls) == 2 and 'pr_url' not in out
    assert 'gh pr create failed (exit 1): GraphQL: HTTP 403' in out['pr_error']
    assert 'gh api pulls failed (exit 1): gh: Not Found (HTTP 404)' in out['pr_error']


@pytest.mark.parametrize('url', ['https://github.com/o/r.git', 'git@github.com:o/r.git', 'ssh://git@github.com/o/r'])
def test_owner_and_repo_come_from_the_push_remote(project, tmp_path, url):
    cfg = rest_ready(project, tmp_path, url)
    gh = ScriptedGh((1, GRAPHQL_403), (0, 'https://github.com/o/r/pull/9'))
    publish_with(project, cfg, gh)
    assert gh.calls[1][:3] == ['gh', 'api', 'repos/o/r/pulls']


def test_a_remote_on_another_host_passes_its_hostname(project, tmp_path):
    cfg = rest_ready(project, tmp_path, 'https://ghe.example.com/o/r.git')
    gh = ScriptedGh((1, GRAPHQL_403), (0, 'https://ghe.example.com/o/r/pull/9'))
    assert publish_with(project, cfg, gh)['pr_url'] == 'https://ghe.example.com/o/r/pull/9'
    assert gh.calls[1][:5] == ['gh', 'api', '--hostname', 'ghe.example.com', 'repos/o/r/pulls']


def test_a_remote_without_owner_and_repo_is_not_tried(project, tmp_path):
    cfg = rest_ready(project, tmp_path, str(tmp_path / 'somewhere.git'))
    gh = ScriptedGh((1, GRAPHQL_403))
    out = publish_with(project, cfg, gh)
    assert len(gh.calls) == 1 and 'gh api pulls not tried' in out['pr_error']
