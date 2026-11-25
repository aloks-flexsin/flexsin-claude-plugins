"""auto-runner plugin (R3): the runner records what got in its way as `friction` events, at most once per signature per item
per run, with fixed-vocabulary subjects and never project content."""
import json

import pytest

from tests.auto_runner.test_friction import install_record, limited_agent
from tests.auto_runner.test_path_and_permissions import refused, use
from tests.auto_runner.test_red_rounds import fixer, suite
from tests.auto_runner.test_runner import ar, code_agent, events, fake_agent, project, set_cfg, with_doc_map  # noqa: F401


def frictions(p):
    """-> [(item, signature)] of the run's friction events; each one valid and free of the test project's paths."""
    found = []
    for e in events(p):
        if e['type'] != 'friction':
            continue
        assert ar.autoevents.validate(e) == []
        text = json.dumps(e['detail'])
        assert str(p) not in text and p.name not in text and '/' not in e['detail']['subject'].replace('Bash:', '')
        found.append((e['item'], ar.autoevents.signature(e['detail'])))
    return found


def denied(*commands, result='Could not do it.'):
    lines = []
    for n, c in enumerate(commands):
        lines += [use(f't{n}', c), refused(f't{n}')]
    return '\n'.join(lines + [json.dumps({'type': 'result', 'is_error': False, 'result': result})])


@pytest.mark.parametrize('call,subject', [
    ('Bash: sed -i x', 'Bash:sed'),
    ('Bash: FOO=1 cd /a/b && pnpm i', 'Bash:pnpm'),
    ('Bash: "$(x)"', 'Bash:other'),
    ('Bash: /usr/local/bin/git status', 'Bash:git'),
    ('Bash: echo "unclosed', 'Bash:other'),
    ('Bash: cd /only/a/dir', 'Bash:other'),
    ('Write: /etc/hosts', 'Write'),
    ({'tool_name': 'Bash', 'tool_input': {'command': 'A=1 B=2 corepack pnpm install'}}, 'Bash:corepack'),
    ({'tool_name': 'WebFetch', 'tool_input': {'url': 'https://example.com/x'}}, 'WebFetch'),
])
def test_the_subject_is_the_tool_or_the_executable_and_never_an_argument(call, subject):
    got = ar.autoevents.friction_subject(call)
    assert got == subject
    assert ar.autoevents.validate_friction(ar.autoevents.friction_detail('permission_denied', got)) == []


def test_refusals_on_two_items_give_one_event_per_item_without_the_argument(project):
    out = denied('sed -i x', 'sed -n secret-arg')               # two refusals of sed in one item: one event
    assert ar.run(project, max_iterations=2, claude=fake_agent(output=out)) == 0
    assert frictions(project) == [('A1', 'permission_denied:Bash:sed'), ('A2', 'permission_denied:Bash:sed')]
    assert 'secret-arg' not in (project / '.auto' / 'events.jsonl').read_text(encoding='utf-8').split('"friction"', 1)[1]


def test_an_early_exit_after_a_refusal_is_a_retry(project):
    assert ar.run(project, max_iterations=1, claude=lambda cmd, cwd: (1, denied('pnpm i'))) == 0
    assert frictions(project) == [('A1', 'permission_denied:Bash:pnpm'), ('A1', 'retry:exit_1')]


def test_a_docs_gate_stop_is_a_gate_stop(project):
    with_doc_map(project)
    assert ar.run(project, max_iterations=2, claude=code_agent(revalidate=False)) == 1
    assert frictions(project) == [('A1', 'gate_stop:docs')]


def test_a_suite_fix_round_is_recorded(project):
    assert ar.run(project, max_iterations=2, claude=fixer(), tests=suite(1)) == 0
    assert frictions(project) == [('A1', 'fix_round:suite')]


def test_a_usage_limit_stop_is_a_usage_stop(project):
    assert ar.run(project, max_iterations=3, claude=limited_agent([])) == ar.USAGE_LIMIT_EXIT
    assert frictions(project) == [('A1', 'usage_stop:limit')]


def test_a_clean_run_records_no_friction(project):
    assert ar.run(project, max_iterations=2, claude=fake_agent()) == 0
    assert frictions(project) == []


# --- refused starts (R4) -------------------------------------------------------------------------------------------------

def refused_starts(p):
    """-> [(item, signature)] of the refused_start events; each one run-level and named after the project folder."""
    found = [(item, sig) for item, sig in frictions(p) if sig.startswith('refused_start')]
    assert all(e['project'] == p.name for e in events(p) if e['type'] == 'friction')
    return found


def deny_nothing(p):
    (p / '.claude' / 'settings.json').write_text(json.dumps({'permissions': {'deny': []}}), encoding='utf-8')


def test_a_newer_installed_version_records_a_refused_start_and_still_refuses(project):
    install_record('99.0.0', 'C:/cache/auto-runner/99.0.0')
    calls = []
    with pytest.raises(ar.RunnerError, match=r'but 99\.0\.0 is installed') as e:
        ar.run(project, max_iterations=1, claude=fake_agent(calls=calls))
    assert 'auto_run.py' in str(e.value) and calls == []
    assert refused_starts(project) == [(None, 'refused_start:newer_installed')]


def test_a_preflight_refusal_for_a_missing_test_command_records_no_tests(project):
    set_cfg(project, test_command='')
    with pytest.raises(ar.RunnerError, match='Preflight refused to start:\n- No test_command'):
        ar.run(project, max_iterations=1, claude=fake_agent())
    assert refused_starts(project) == [(None, 'refused_start:no_tests')]


def test_only_the_first_failed_group_is_recorded_also_on_a_dry_run(project):
    set_cfg(project, test_command='')
    deny_nothing(project)
    with pytest.raises(ar.RunnerError, match='must deny'):
        ar.run(project, dry_run=True)
    assert refused_starts(project) == [(None, 'refused_start:no_tests')]


def test_preflight_groups_name_the_group_of_each_problem(project):
    deny_nothing(project)
    cfg = ar.autocfg.load(project)
    cfg['test_command'] = ''
    groups = ar.autosafe.preflight_groups(project, cfg, 'main')
    assert [g for g, _ in groups] == ['no_tests', 'settings']
    assert 'test_command' in groups[0][1] and 'must deny' in groups[1][1]
    assert ar.autosafe.preflight(project, cfg, 'main') == [m for _, m in groups]


def test_a_ready_project_has_no_preflight_groups_and_records_no_refusal(project):
    assert ar.autosafe.preflight_groups(project, ar.autocfg.load(project), 'main') == []
    assert ar.run(project, max_iterations=1, claude=fake_agent()) == 0
    assert refused_starts(project) == []
