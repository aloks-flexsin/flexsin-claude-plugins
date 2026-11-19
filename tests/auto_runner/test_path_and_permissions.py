"""auto-runner plugin (0.12.3): a project's tool folders go first on PATH for everything the runner starts, and an item the
agent could not finish because a tool call was refused says which call and how to allow it."""
import json
import os

import pytest

from tests.auto_runner.test_runner import GREEN, ar, fake_agent, project, project_statuses, set_cfg, sh, \
    worktree  # noqa: F401  (project is a fixture)


def shim_project(project):
    (project / 'tools' / 'bin').mkdir(parents=True)
    (project / 'tools' / 'bin' / 'pnpm.cmd').write_text('@echo shim\n', encoding='utf-8')
    sh(project, 'add', 'tools')
    sh(project, '-c', 'user.email=t@example.com', '-c', 'user.name=T', 'commit', '-q', '-m', 'shim')
    set_cfg(project, test_command=GREEN, path_prepend=['tools/bin'])


def first_on_path(env):
    key = next(k for k in env if k.upper() == 'PATH')
    return env[key].split(os.pathsep)[0]


# --- path_prepend --------------------------------------------------------------------------------------------------------

def test_the_agent_and_the_suite_get_the_worktree_shims_first_on_path(project):
    shim_project(project)
    seen = {}
    inner = fake_agent()

    def agent(cmd, cwd):
        seen['agent'] = first_on_path(ar.child_env())
        return inner(cmd, cwd)

    def suite(command, cwd):
        seen['suite'] = first_on_path(ar.child_env())
        return 0, '1 passed'
    assert ar.run(project, max_iterations=1, claude=agent, tests=suite) == 0
    expect = str((worktree(project) / 'tools' / 'bin').resolve())
    assert seen == {'agent': expect, 'suite': expect}
    assert ar.PATH_PREPEND == [] and first_on_path(ar.child_env()) != expect   # cleared when the run ends


def test_a_real_child_finds_the_shim(project, tmp_path):
    shim_project(project)
    found = {}
    inner = fake_agent()

    def agent(cmd, cwd):
        probe = ar.run_claude(['python', '-c', 'import os; print(os.environ["PATH"].split(os.pathsep)[0])'], cwd)
        found['path'] = probe[1].strip()
        return inner(cmd, cwd)
    assert ar.run(project, max_iterations=1, claude=agent) == 0
    assert found['path'].lower().endswith(os.path.join('tools', 'bin').lower())


@pytest.mark.parametrize('entry,problem', [('/usr/bin', 'inside the project'), ('../elsewhere', 'inside the project'),
                                           ('tools/missing', 'not a folder'), (' ', 'inside the project')])
def test_preflight_refuses_a_bad_path_prepend(project, entry, problem):
    problems = ar.autosafe.path_prepend_problems(project, {'path_prepend': [entry]})
    assert len(problems) == 1 and problem in problems[0]


def test_path_prepend_is_a_known_list_setting(project):
    shim_project(project)
    assert ar.autocfg.load(project)['path_prepend'] == ['tools/bin']


# --- refused tool calls ---------------------------------------------------------------------------------------------------

def use(i, command):
    return json.dumps({'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'id': i, 'name': 'Bash', 'input': {'command': command}}]}})


def refused(i, text="Claude requested permissions to use Bash, but you haven't granted it yet."):
    return json.dumps({'type': 'user', 'message': {'content': [
        {'type': 'tool_result', 'tool_use_id': i, 'is_error': True, 'content': text}]}})


def test_denials_are_read_from_the_transcript():
    out = '\n'.join([use('t1', 'corepack pnpm install oidc-provider'), refused('t1'),
                     use('t2', 'pnpm test'), json.dumps({'type': 'user', 'message': {'content': [
                         {'type': 'tool_result', 'tool_use_id': 't2', 'is_error': True, 'content': '3 failed'}]}})])
    assert ar.permission_denials(out) == ['Bash: corepack pnpm install oidc-provider']


def test_denials_are_read_from_the_result_when_the_cli_lists_them():
    out = json.dumps({'type': 'result', 'is_error': False, 'result': 'stopped', 'permission_denials': [
        {'tool_name': 'Bash', 'tool_use_id': 'x', 'tool_input': {'command': 'corepack pnpm install'}},
        {'tool_name': 'Write', 'tool_use_id': 'y', 'tool_input': {'file_path': '/etc/hosts'}}]})
    assert ar.permission_denials(out) == ['Bash: corepack pnpm install', 'Write: /etc/hosts']


def test_a_failing_command_is_not_a_denial():
    out = '\n'.join([use('t1', 'pnpm build'), refused('t1', 'Error: build failed')])
    assert ar.permission_denials(out) == []


def test_an_item_stopped_by_a_refusal_says_which_call_and_how_to_allow_it(project, capsys):
    out = '\n'.join([use('t1', 'corepack pnpm install oidc-provider'), refused('t1'),
                     json.dumps({'type': 'result', 'is_error': False, 'result': 'Could not install.'})])
    assert ar.run(project, max_iterations=1, claude=lambda cmd, cwd: (0, out)) == 0
    state, detail = project_statuses(project)['A1']
    assert state == 'blocked' and 'needs permission for Bash: corepack pnpm install oidc-provider' in detail
    assert 'path_prepend' in detail and '.claude/settings.json' in detail
    assert 'refused permission for Bash: corepack pnpm install' in capsys.readouterr().out
