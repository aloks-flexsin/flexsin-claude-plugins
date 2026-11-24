"""auto-runner plugin: the headless planner (bin/autoplan.py, watch mode) with a fake claude and the fake task service
(fake_central.FakeTasks): no network, no CLI."""
import importlib.util
import json
from pathlib import Path

import pytest

from tests.auto_runner.fake_central import FakeTasks

BIN = Path(__file__).resolve().parents[2] / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autorunner_plan_t', BIN / 'autoplan.py')
ap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ap)

ENV = {'AUTO_RUNNER_TOKEN': 'arp_test-token', 'AUTO_RUNNER_USER': 'alok', 'AUTO_RUNNER_ACCOUNT': 'bhawish'}
PLAN = """## Intent
CSV export for the reports page.

## Queue items
- [ ] **E1 Export.** Add the CSV export to the reports page.
  *Acceptance:* tests/test_export.py downloads a CSV.
  *Slice:* 1 Export

## Needs a person
None.
"""
FINDING = {'item': 'E1', 'kind': 'untestable_acceptance', 'detail': 'the test file is not named clearly.',
           'fix': 'name the test.'}


def reply(text, cost=0.1):
    return json.dumps({'type': 'result', 'result': text, 'total_cost_usd': cost})


def spec_reply(findings):
    return json.dumps({'schema': 'spec.v1', 'ok': not findings, 'findings': findings})


class FakeClaude:
    """Answers the planner with `plans` (one per call) and the spec check with `checks` (one per call)."""

    def __init__(self, plans, checks=(), cost=0.1):
        self.plans, self.checks, self.cost = list(plans), list(checks), cost
        self.prompts, self.cmds = [], []

    def __call__(self, cmd, cwd):
        self.cmds.append(cmd)
        text = cmd[cmd.index('-p') + 1]
        self.prompts.append(text)
        if text.startswith('You review a work plan'):
            return 0, reply(spec_reply(self.checks.pop(0) if self.checks else []), self.cost)
        return 0, reply(self.plans.pop(0), self.cost)

    def planner_prompts(self):
        return [p for p in self.prompts if not p.startswith('You review a work plan')]


@pytest.fixture
def setup(tmp_path):
    p = tmp_path / 'proj'
    (p / '.claude').mkdir(parents=True)
    (p / '.claude' / 'auto.json').write_text(json.dumps({
        'central': {'enabled': True, 'url': 'http://central.test', 'project': 'acme'},
        'models': {'planner': 'opus'}, 'watch': {'plan_budget_usd': 1}}), encoding='utf-8')
    claims = p / '.auto' / 'run' / 'tasks'
    claims.mkdir(parents=True)
    (claims / '7.json').write_text(json.dumps({'number': 7, 'claim': 'claim-1', 'title': 'CSV export'}), encoding='utf-8')
    svc = FakeTasks()
    svc.add(7, 'CSV export', 'Let managers export the report as CSV.')
    return p, svc


def run(setup, claude):
    p, svc = setup
    return ap.plan(p, 7, claude, ENV, svc.send)


def test_plan_is_posted_with_its_items(setup):
    p, svc = setup
    claude = FakeClaude([PLAN])
    r = run(setup, claude)
    assert r['status'] == 'posted' and r['findings'] == []
    assert svc.tasks[7]['status'] == 'plan_review'
    [post] = svc.posted
    assert '**E1 Export.**' in post['items'] and '*Slice:* 1 Export' in post['items'] and '## Intent' not in post['items']
    assert post['plan'].startswith('## Intent') and 'None.' in post['plan']
    planner_cmd = claude.cmds[0]
    assert planner_cmd[planner_cmd.index('--model') + 1] == 'opus'
    assert 'Edit' in planner_cmd and planner_cmd.index('Edit') > planner_cmd.index('--disallowedTools')
    assert r['spent_usd'] == pytest.approx(0.2)                     # planner and spec check both counted


def test_reply_without_queue_items_is_refused(setup):
    p, svc = setup
    r = run(setup, FakeClaude(['## Intent\nI would ask the user first.\n']))
    assert r['status'] == 'refused' and 'Queue items' in r['reason']
    assert svc.posted == [] and svc.tasks[7]['status'] == 'planning'


def test_cost_cap_posts_nothing_and_records_the_reason(setup):
    p, svc = setup
    r = run(setup, FakeClaude([PLAN], cost=0.6))                   # planner 0.6 + spec check 0.6 >= 1
    assert r['status'] == 'budget'
    assert svc.posted == []
    watch = json.loads((p / '.auto' / 'run' / 'watch.json').read_text(encoding='utf-8'))
    assert watch['state'] == 'plan_budget' and watch['task'] == 7 and 'plan_budget_usd' in watch['reason']
    assert not (p / '.auto' / 'run' / 'tasks' / '7.json').exists()  # no more beats or posts on that claim


def test_cost_cap_keeps_other_watch_fields(setup):
    p, svc = setup
    (p / '.auto' / 'run' / 'watch.json').write_text(json.dumps({'label': 'laptop'}), encoding='utf-8')
    run(setup, FakeClaude([PLAN], cost=5))
    watch = json.loads((p / '.auto' / 'run' / 'watch.json').read_text(encoding='utf-8'))
    assert watch['label'] == 'laptop' and watch['state'] == 'plan_budget'


def test_one_revision_then_findings_go_under_needs_a_person(setup):
    p, svc = setup
    claude = FakeClaude([PLAN, PLAN.replace('Add the CSV', 'Add the revised CSV')], checks=[[FINDING], [FINDING]])
    r = run(setup, claude)
    assert r['status'] == 'posted' and r['findings'] == [FINDING]
    assert len(claude.planner_prompts()) == 2                      # one revision at most
    assert 'the test file is not named clearly.' in claude.planner_prompts()[1]
    plan = svc.posted[0]['plan']
    needs = ap.autospec.section(plan, 'Needs a person')
    assert 'E1 [untestable_acceptance]' in needs and 'name the test.' in needs and 'None.' not in needs
    assert 'revised CSV' in svc.posted[0]['items']


def test_revision_that_fixes_the_findings_is_posted_clean(setup):
    p, svc = setup
    r = run(setup, FakeClaude([PLAN, PLAN], checks=[[FINDING], []]))
    assert r['status'] == 'posted' and r['findings'] == []
    assert ap.autospec.section(svc.posted[0]['plan'], 'Needs a person') == 'None.'


def test_sent_back_comment_reaches_the_prompt(setup):
    p, svc = setup
    svc.tasks[7]['comment'] = 'Also cover the PDF export please.'
    claude = FakeClaude([PLAN])
    run(setup, claude)
    prompt = claude.planner_prompts()[0]
    assert 'Also cover the PDF export please.' in prompt and 'Let managers export the report as CSV.' in prompt


def test_prompt_file_is_loaded(setup, monkeypatch, tmp_path):
    claude = FakeClaude([PLAN])
    run(setup, claude)
    assert ap.PROMPT_FILE.read_text(encoding='utf-8').strip() in claude.planner_prompts()[0]
    own = tmp_path / 'planner.md'
    own.write_text('PLANNER-PROMPT-MARKER', encoding='utf-8')
    monkeypatch.setattr(ap, 'PROMPT_FILE', own)
    claude = FakeClaude([PLAN])
    run(setup, claude)
    assert claude.planner_prompts()[0].startswith('PLANNER-PROMPT-MARKER')


def test_no_claim_on_this_computer_is_not_planned(setup):
    p, svc = setup
    (p / '.auto' / 'run' / 'tasks' / '7.json').unlink()
    claude = FakeClaude([PLAN])
    r = run(setup, claude)
    assert r['status'] == 'failed' and claude.prompts == [] and svc.posted == []


def test_add_needs_creates_the_section_when_missing():
    out = ap.add_needs('## Intent\nX.\n', ['decide the colour'])
    assert ap.autospec.section(out, 'Needs a person') == '- decide the colour'
