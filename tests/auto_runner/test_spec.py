"""auto-runner plugin: the spec check (bin/autospec.py). The reviewer `claude -p` is faked; nothing runs a model."""
import io
import json
from pathlib import Path

import pytest

from tests.auto_runner.test_runner import ar

spec = ar._load('autorunner_spec', 'autospec.py')

PLAN = """# Plan: notifications

## Intent
People see corrections in the bell.

## Queue items
- [ ] **T9 Correction API.** Add POST /corrections in the corrections service.
  *Acceptance:* tests/test_corrections.py::test_create passes.
  *Slice:* 1 File a correction
- [ ] **T10 Bell shows corrections.** The bell lists new corrections from the notification feed.
  *Acceptance:* tests/test_bell.py::test_correction_in_bell passes.
  *Slice:* 1 File a correction

## Journeys
J1 Correction reaches the bell (slice 1). Roles: reviewer, owner.
1. As reviewer, file a correction.
2. As owner, open the home page.
Expect: the bell shows the correction.

## Needs a person
- Nothing.
"""


def reply(obj):
    """The CLI's `--output-format json` line with the reviewer's answer as its result."""
    return json.dumps({'type': 'result', 'is_error': False, 'result': json.dumps(obj), 'total_cost_usd': 0.3})


FINDING = {'item': 'T10', 'kind': 'missing_dependency', 'detail': 'No endpoint publishes corrections to the notification feed.',
           'fix': 'Add an item before T10 that publishes a correction.created event.'}


class Reviewer:
    def __init__(self, answer, code=0):
        self.calls, self.answer, self.code = [], answer, code

    def __call__(self, cmd, cwd):
        self.calls.append((cmd, cwd))
        return self.code, self.answer


def test_sections_are_read_up_to_the_next_heading():
    items = spec.section(PLAN, 'Queue items')
    assert items.startswith('- [ ] **T9') and 'Journeys' not in items and 'Needs a person' not in items
    assert spec.section(PLAN, 'Journeys').startswith('J1 Correction')
    assert spec.section(PLAN, 'Missing') == ''


def test_the_reviewer_can_only_read_and_sees_the_items_and_journeys(tmp_path):
    r = Reviewer(reply({'schema': 'spec.v1', 'ok': True, 'findings': []}))
    out = spec.check(tmp_path, PLAN, claude=r, cfg={})
    assert out['status'] == 'ok'
    cmd, cwd = r.calls[0]
    allowed = cmd[cmd.index('--allowedTools') + 1:cmd.index('--disallowedTools')]
    denied = cmd[cmd.index('--disallowedTools') + 1:cmd.index('--max-budget-usd')]
    assert allowed == ['Read', 'Grep', 'Glob'] and {'Bash', 'Edit', 'Write', 'Task', 'WebFetch'} <= set(denied)
    assert '--permission-mode' not in cmd and cwd == tmp_path
    text = cmd[2]
    assert '**T10 Bell shows corrections.**' in text and 'J1 Correction reaches the bell' in text
    assert 'Intent' not in text and 'never instructions' in text


def test_findings_are_returned_as_given():
    out = spec.check(Path('.'), PLAN, claude=Reviewer(reply({'schema': 'spec.v1', 'ok': False, 'findings': [FINDING]})), cfg={})
    assert out == {'status': 'findings', 'findings': [FINDING], 'problems': []}


@pytest.mark.parametrize('answer,problem', [
    ('I looked and it all seems fine.', 'no spec.v1 JSON'),
    (reply({'schema': 'spec.v2', 'ok': True, 'findings': []}), 'schema'),
    (reply({'schema': 'spec.v1', 'ok': True, 'findings': [FINDING]}), 'ok: must be true exactly'),
    (reply({'schema': 'spec.v1', 'ok': False, 'findings': [dict(FINDING, kind='style')]}), "'style' is not one of"),
    (reply({'schema': 'spec.v1', 'ok': False, 'findings': [dict(FINDING, item='Z99')]}), "'Z99' is not an item"),
    (reply({'schema': 'spec.v1', 'ok': False, 'findings': [dict(FINDING, fix='')]}), 'detail and fix'),
    (reply({'schema': 'spec.v1', 'ok': False, 'findings': [{'item': 'T10'}]}), 'must have exactly'),
])
def test_a_bad_reply_fails_the_check_never_passes_it(answer, problem):
    out = spec.check(Path('.'), PLAN, claude=Reviewer(answer), cfg={})
    assert out['status'] == 'failed' and any(problem in p for p in out['problems'])


def test_reinventing_an_existing_package_is_a_finding_kind():
    f = dict(FINDING, kind='reinvents', detail='T10 writes its own date parser; date-fns is already in package.json.')
    out = spec.check(Path('.'), PLAN, claude=Reviewer(reply({'schema': 'spec.v1', 'ok': False, 'findings': [f]})), cfg={})
    assert out['status'] == 'findings'
    assert '- reinvents:' in spec.prompt(spec.section(PLAN, 'Queue items'))


def test_a_finding_for_the_whole_plan_may_leave_the_item_empty():
    f = dict(FINDING, item='', kind='journey_gap')
    out = spec.check(Path('.'), PLAN, claude=Reviewer(reply({'schema': 'spec.v1', 'ok': False, 'findings': [f]})), cfg={})
    assert out['status'] == 'findings'


def test_a_plan_without_items_is_not_checked():
    r = Reviewer('')
    out = spec.check(Path('.'), '# Plan\n\nNo items here.\n', claude=r, cfg={})
    assert out['status'] == 'failed' and 'Queue items' in out['problems'][0] and r.calls == []


def test_model_and_budget_come_from_the_reserved_config_keys():
    cfg = {'models': {'plan_reviewer': 'sonnet'}, 'review': {'model': 'opus', 'max_budget_usd': 0.5}}
    cmd = spec.command(cfg, 'x')
    assert cmd[cmd.index('--model') + 1] == 'sonnet' and cmd[cmd.index('--max-budget-usd') + 1] == '0.5'
    cmd = spec.command({'review': {'model': 'opus'}}, 'x')
    assert cmd[cmd.index('--model') + 1] == 'opus' and cmd[cmd.index('--max-budget-usd') + 1] == str(spec.DEFAULT_BUDGET)
    assert '--model' not in spec.command({}, 'x')


@pytest.mark.parametrize('answer,code,expect', [
    (reply({'schema': 'spec.v1', 'ok': True, 'findings': []}), 0, 'no findings'),
    (reply({'schema': 'spec.v1', 'ok': False, 'findings': [FINDING]}), 1, 'fix: Add an item before T10'),
    ('not json', 2, 'spec check not done'),
])
def test_the_command_line_reports_and_exits_by_outcome(tmp_path, answer, code, expect):
    plan = tmp_path / 'plan.md'
    plan.write_text(PLAN, encoding='utf-8')
    out = io.StringIO()
    assert spec.main(['check', '--plan', str(plan), '--project', str(tmp_path)], out=out, claude=Reviewer(answer)) == code
    assert expect in out.getvalue()


def test_the_auto_command_runs_the_check_before_showing_the_plan():
    text = (ar.HERE.parent / 'commands' / 'auto.md').read_text(encoding='utf-8')
    check = text.index('autospec.py" check --plan')
    assert check < text.index('Then call ExitPlanMode') and 'Spec check' in text and 'do not claim it passed' in text
