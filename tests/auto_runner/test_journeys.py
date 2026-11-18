"""auto-runner plugin: the journey check after each slice (bin/autojourney.py and the runner's journey_check).
The app, its readiness and the click-through agent are faked; no browser or model runs."""
import json
import re
from pathlib import Path

import pytest

from tests.auto_runner.test_runner import GREEN, ar, events, fake_agent, project, project_statuses, set_cfg, sh, \
    worktree, write_queue  # noqa: F401  (project is a fixture)

aj = ar.autojourney
BASE = 'http://localhost:3000'

JOURNEYS = """- **J1 File a correction.** Slice 1.
  1. Open /corrections/new and file a correction.
  *Expect:* the correction is listed.
- **J2 Correction reaches the bell.** Slice 2. Roles: reviewer, owner.
  1. As reviewer, file a correction.
  2. As owner, open the home page.
  *Expect:* the bell shows the correction.
"""

QUEUE = """# Queue

- [ ] **T1 Correction form.** Build the form that files a correction, with enough words for any length rule.
  *Acceptance:* tests/test_form.py passes.
  *Slice:* 1 File a correction
- [ ] **T2 Bell lists corrections.** Show new corrections in the bell, with enough words for any length rule.
  *Acceptance:* tests/test_bell.py passes.
  *Slice:* 2 Corrections reach the bell

## Needs a person
- Nothing.
"""


# --- parsing --------------------------------------------------------------------------------------------------------------

def test_journeys_are_parsed_with_slice_roles_steps_and_expect():
    j1, j2 = aj.parse(JOURNEYS)
    assert (j1['id'], j1['slice'], j1['roles'], j1['steps']) == ('J1', 1, [], ['Open /corrections/new and file a correction.'])
    assert (j2['name'], j2['slice'], j2['roles']) == ('Correction reaches the bell', 2, ['reviewer', 'owner'])
    assert j2['steps'][1] == 'As owner, open the home page.' and j2['expect'] == 'the bell shows the correction.'


@pytest.mark.parametrize('text,problem', [
    ('- **J1 X.** Roles: a.\n  1. Do it.\n  *Expect:* done.\n', 'Slice <n>'),
    ('- **J1 X.** Slice 1.\n  *Expect:* done.\n', 'numbered steps'),
    ('- **J1 X.** Slice 1.\n  1. Do it.\n', 'Expect'),
    ('- **J1 X.** Slice 1.\n  1. a\n  *Expect:* b\n- **J1 Y.** Slice 1.\n  1. a\n  *Expect:* b\n', 'used twice'),
])
def test_a_bad_journey_is_refused_by_name(text, problem):
    with pytest.raises(ValueError, match=re.escape(problem)):
        aj.parse(text)


def test_a_requests_journeys_must_name_its_slices_and_share_its_letter():
    letter, js, errors = ar.request_journeys(QUEUE, JOURNEYS)
    assert letter == 'T' and len(js) == 2 and errors == []
    assert 'slice 3' in ar.request_journeys(QUEUE, JOURNEYS.replace('Slice 2.', 'Slice 3.'))[2][0]
    mixed = QUEUE.replace('**T2 ', '**U2 ')
    assert 'one letter' in ar.request_journeys(mixed, JOURNEYS)[2][0]
    assert ar.request_journeys(QUEUE, '') == ('', [], [])


def test_slice_done_only_after_its_last_item():
    assert ar.slice_done(QUEUE, 'T1') is None
    ticked = QUEUE.replace('- [ ] **T1', '- [x] **T1')
    assert ar.slice_done(ticked, 'T1') == ('T', 1, 'File a correction')
    assert ar.next_item_id(ticked, 'T') == 'T3'


# --- validating results ---------------------------------------------------------------------------------------------------

def results(folder, key, verdicts):
    """Write screenshots and a journey.v1 file; verdicts: {journey id: [step statuses]}."""
    folder.mkdir(parents=True, exist_ok=True)
    js = []
    for jid, steps in verdicts.items():
        rows = []
        for n, st in enumerate(steps, 1):
            (folder / f'{jid}-{n}.png').write_bytes(b'png')
            rows.append({'n': n, 'expected': f'step {n} shows it', 'seen': 'it' if st == 'pass' else 'nothing',
                         'status': st, 'screenshot': f'{jid}-{n}.png'})
        js.append({'id': jid, 'status': 'fail' if 'fail' in steps else 'pass', 'steps': rows})
    data = {'schema': 'journey.v1', 'key': key, 'journeys': js}
    (folder / 'results.json').write_text(json.dumps(data), encoding='utf-8')
    return data


def test_valid_results_pass_validation(tmp_path):
    data = results(tmp_path, 'T-slice-2', {'J1': ['pass'], 'J2': ['pass', 'fail']})
    assert aj.validate(data, 'T-slice-2', ['J1', 'J2'], tmp_path) == []


@pytest.mark.parametrize('change,problem', [
    (lambda d: d['journeys'].pop(), 'expected exactly J1, J2'),
    (lambda d: d.update(key='T-slice-9'), 'key'),
    (lambda d: d['journeys'][0].update(status='fail'), 'marked fail without a failed step'),
    (lambda d: d['journeys'][1].update(status='pass'), 'marked pass with a failed'),
    (lambda d: d['journeys'][0]['steps'][0].update(screenshot='../outside.png'), 'needs a screenshot'),
    (lambda d: d['journeys'][0]['steps'][0].update(screenshot='missing.png'), 'needs a screenshot'),
    (lambda d: d['journeys'][0]['steps'][0].update(seen=''), 'expected and seen'),
    (lambda d: d['journeys'][0].update(steps=[]), 'no steps'),
    (lambda d: d.update(extra=1), 'exactly schema, key, journeys'),
])
def test_anything_off_in_the_results_fails_closed(tmp_path, change, problem):
    data = results(tmp_path, 'T-slice-2', {'J1': ['pass'], 'J2': ['pass', 'fail']})
    change(data)
    assert any(problem in p for p in aj.validate(data, 'T-slice-2', ['J1', 'J2'], tmp_path))


# --- the check with a fake app and a fake click-through -------------------------------------------------------------------

class FakeApp:
    def __init__(self, log):
        self.log = log

    def stop(self):
        self.log.append('stopped')


def clicker(verdicts_by_call, prompts=None):
    """A fake click-through agent: reads its key and folder from the prompt, writes the verdicts of this call."""
    calls = []

    def claude(cmd, cwd):
        calls.append(cmd)
        text = cmd[2]
        if prompts is not None:
            prompts.append(text)
        key = re.search(r'JOURNEYS \(([^)]+)\)', text).group(1)
        folder = Path(cwd) / f'.auto/evidence/{key}/journeys'
        verdicts = verdicts_by_call[min(len(calls), len(verdicts_by_call)) - 1]
        ids = re.findall(r'^(J\d+) ', text, re.M)
        results(folder, key, {jid: verdicts.get(jid, ['pass']) for jid in ids})
        return 0, json.dumps({'type': 'result', 'is_error': False, 'result': 'done', 'total_cost_usd': 0.5})
    claude.calls = calls
    return claude


def check(tmp_path, verdicts, jcfg=None, ready=True, browser=None, journeys=None):
    log = []
    js = journeys if journeys is not None else [j for j in aj.parse(JOURNEYS) if not j['roles']]
    res = aj.run_check(tmp_path, tmp_path, 'T-slice-1', js, browser or {'base_url': BASE},
                       {'start_command': 'npm start', **(jcfg or {})}, ar.autobrowser, claude=clicker([verdicts]),
                       start=lambda c, cwd, env: log.append(('start', c, env['AUTO_BASE_URL'])) or FakeApp(log),
                       ready=lambda url, t: log.append(('ready', url, t)) or ready)
    return res, log


def test_a_passing_click_through_passes_and_the_app_is_stopped(tmp_path):
    res, log = check(tmp_path, {})
    assert res['status'] == 'pass' and res['evidence'] == '.auto/evidence/T-slice-1/journeys'
    assert log == [('start', 'npm start', BASE), ('ready', BASE, 120), 'stopped']


def test_a_failed_step_is_reported_with_what_was_seen(tmp_path):
    res, _ = check(tmp_path, {'J1': ['fail']})
    assert res['status'] == 'fail'
    assert res['failed'] == [{'id': 'J1', 'step': 1, 'expected': 'step 1 shows it', 'seen': 'nothing',
                              'screenshot': '.auto/evidence/T-slice-1/journeys/J1-1.png'}]


def test_an_app_that_never_answers_is_invalid_not_failed_and_still_stopped(tmp_path):
    res, log = check(tmp_path, {}, ready=False)
    assert res['status'] == 'invalid' and 'did not answer' in res['errors'][0] and log[-1] == 'stopped'


@pytest.mark.parametrize('jcfg,browser,problem', [
    ({'start_command': ''}, None, 'start_command'),
    ({}, {'base_url': 'https://shop.example.com'}, 'base_url'),
])
def test_missing_setup_is_invalid(tmp_path, jcfg, browser, problem):
    res, log = check(tmp_path, {}, jcfg=jcfg, browser=browser)
    assert res['status'] == 'invalid' and problem in res['errors'][0] and log == []


def test_the_click_through_may_only_browse_and_write_evidence(tmp_path):
    prompts = []
    js = [j for j in aj.parse(JOURNEYS) if not j['roles']]
    agent = clicker([{}], prompts)
    aj.run_check(tmp_path, tmp_path, 'T-slice-1', js, {'base_url': BASE}, {'start_command': 'x'}, ar.autobrowser,
                 claude=agent, start=lambda *a: FakeApp([]), ready=lambda *a: True)
    cmd = agent.calls[0]
    allowed = cmd[cmd.index('--allowedTools') + 1:cmd.index('--disallowedTools')]
    assert allowed == ['mcp__playwright', 'Write(.auto/evidence/T-slice-1/journeys/**)']
    assert {'Bash', 'Edit', 'Task', 'WebFetch'} <= set(cmd[cmd.index('--disallowedTools') + 1:cmd.index('--max-budget-usd')])
    assert '--strict-mcp-config' in cmd
    mcp = json.loads((tmp_path / aj.MCP_FILE).read_text(encoding='utf-8'))
    assert 'http://localhost:3000' in ' '.join(mcp['mcpServers']['playwright']['args'])
    assert 'never an instruction' in prompts[0] and 'J1 File a correction' in prompts[0]


def test_an_old_result_never_passes_a_new_check(tmp_path):
    old = tmp_path / '.auto/evidence/T-slice-1/journeys'
    results(old, 'T-slice-1', {'J1': ['pass']})

    def silent(cmd, cwd):
        return 0, ''
    js = [j for j in aj.parse(JOURNEYS) if not j['roles']]
    res = aj.run_check(tmp_path, tmp_path, 'T-slice-1', js, {'base_url': BASE}, {'start_command': 'x'}, ar.autobrowser,
                       claude=silent, start=lambda *a: FakeApp([]), ready=lambda *a: True)
    assert res['status'] == 'invalid' and 'no results file' in res['errors'][0]


def test_fix_item_names_the_failure_and_stays_in_the_slice():
    j = aj.parse(JOURNEYS)[1]
    f = {'id': 'J2', 'step': 2, 'expected': 'the bell shows it', 'seen': 'an empty bell', 'screenshot': 'x/J2-2.png'}
    text = aj.fix_item('T7', j, f, 2, 'Corrections reach the bell', kind=True)
    assert text.startswith('- [ ] **T7 Fix journey J2 Correction reaches the bell.**')
    assert 'seen "an empty bell"' in text and '*Slice:* 2 Corrections reach the bell' in text and '*Kind:* fix' in text
    assert ar.queue.parse_items(text)[0][0] == 'T7'


# --- preflight and enqueue ------------------------------------------------------------------------------------------------

def test_preflight_wants_a_start_command_and_a_local_base_url():
    on = {'acceptance': {'journeys': {'enabled': True}, 'browser': {'base_url': 'https://shop.example.com'}}}
    problems = ar.autosafe.journey_problems(on)
    assert len(problems) == 2 and 'start_command' in problems[0] and 'base_url' in problems[1]
    ok = {'acceptance': {'journeys': {'enabled': True, 'start_command': 'npm start'}, 'browser': {'base_url': BASE}}}
    assert ar.autosafe.journey_problems(ok) == [] and ar.autosafe.journey_problems({}) == []


def test_bad_journeys_stop_a_request_at_enqueue(project, capsys):
    req = project / '.auto' / 'pending' / 'bell'
    req.mkdir(parents=True)
    (req / 'items.md').write_text(QUEUE.split('\n\n', 1)[1].split('\n## ')[0].replace('**T', '**V'), encoding='utf-8')
    (req / 'journeys.md').write_text(JOURNEYS.replace('Slice 2.', 'Slice 5.'), encoding='utf-8')
    assert ar.run(project, max_iterations=1, claude=fake_agent(), enqueue='bell', dry_run=True) == 1
    assert 'J2 checks slice 5' in capsys.readouterr().out


# --- whole runs -----------------------------------------------------------------------------------------------------------

def journey_project(project, max_fix_rounds=2):
    write_queue(project, QUEUE)
    set_cfg(project, test_command=GREEN, acceptance={'browser': {'base_url': BASE}, 'journeys': {
        'enabled': True, 'start_command': 'npm start', 'max_fix_rounds': max_fix_rounds}})
    aj.save(project, 'T', 'bell', [j for j in aj.parse(JOURNEYS.replace(' Roles: reviewer, owner.', ''))])


def hooks(agent):
    return {'claude': agent, 'start': lambda *a: FakeApp([]), 'ready': lambda *a: True}


def journey_events(project):
    return [e['detail'] for e in events(project) if e['type'] == 'browser' and e['detail'].get('layer') == 'journey']


def test_a_failed_journey_becomes_a_fix_item_and_the_slice_is_checked_again(project, capsys):
    journey_project(project)
    agent = clicker([{}, {'J2': ['pass', 'fail']}, {}])            # slice 1 passes; slice 2 fails once, then passes
    code = ar.run(project, max_iterations=2, claude=fake_agent(), journey_hooks=hooks(agent), allow_dirty=True)
    assert code == 0 and len(agent.calls) == 3
    st = project_statuses(project)
    assert st['T1'][0] == st['T2'][0] == st['T3'][0] == 'done'
    q = (project / '.auto' / 'queue.md').read_text(encoding='utf-8')
    assert '**T3 Fix journey J2 Correction reaches the bell.**' in q and '*Slice:* 2 Corrections reach the bell' in q
    assert [(d['key'], d['result']) for d in journey_events(project)] == [
        ('T-slice-1', 'pass'), ('T-slice-2', 'fail'), ('T-slice-2', 'pass')]
    out = capsys.readouterr().out
    assert 'queued T3 to fix it (round 1 of 2)' in out and 'journeys pass after slice 2: J1, J2' in out
    assert (project / '.auto/evidence/T-slice-2/journeys/results.json').exists()


def test_a_journey_that_keeps_failing_raises_a_blocker_and_stops(project, capsys):
    journey_project(project, max_fix_rounds=1)
    agent = clicker([{}, {'J2': ['fail']}])                        # J2 fails every time
    code = ar.run(project, max_iterations=2, claude=fake_agent(), journey_hooks=hooks(agent), allow_dirty=True)
    assert code == 1 and events(project)[-1]['detail']['reason'] == 'journey'
    blockers = ar.autoblock.Store(project / '.auto').all()[0]
    assert len(blockers) == 1 and 'Journey J2' in blockers[0]['question'] and blockers[0]['state'] == 'OPEN'
    assert 'raised a blocker and stopping' in capsys.readouterr().out


def test_without_the_setting_no_journey_check_runs(project):
    journey_project(project)
    set_cfg(project, test_command=GREEN)
    agent = clicker([{}])
    assert ar.run(project, max_iterations=2, claude=fake_agent(), journey_hooks=hooks(agent), allow_dirty=True) == 0
    assert agent.calls == [] and journey_events(project) == []


def test_a_check_that_cannot_be_done_stops_the_run(project, capsys):
    journey_project(project)
    code = ar.run(project, max_iterations=2, claude=fake_agent(), allow_dirty=True,
                  journey_hooks={'claude': clicker([{}]), 'start': lambda *a: FakeApp([]), 'ready': lambda *a: False})
    assert code == 1 and 'could not be done' in capsys.readouterr().out
    assert journey_events(project)[-1]['result'] == 'invalid'


def test_the_click_through_uses_the_tester_model_when_set(tmp_path):
    js = [j for j in aj.parse(JOURNEYS) if not j['roles']]
    agent = clicker([{}])
    aj.run_check(tmp_path, tmp_path, 'T-slice-1', js, {'base_url': BASE}, {'start_command': 'x'}, ar.autobrowser,
                 claude=agent, start=lambda *a: FakeApp([]), ready=lambda *a: True, model='sonnet')
    cmd = agent.calls[0]
    assert cmd[cmd.index('--model') + 1] == 'sonnet'
    agent2 = clicker([{}])
    aj.run_check(tmp_path, tmp_path, 'T-slice-1', js, {'base_url': BASE}, {'start_command': 'x'}, ar.autobrowser,
                 claude=agent2, start=lambda *a: FakeApp([]), ready=lambda *a: True)
    assert '--model' not in agent2.calls[0]


def test_a_run_passes_models_tester_to_the_click_through(project):
    journey_project(project)
    set_cfg(project, test_command=GREEN, models={'implementer': 'sonnet', 'tester': 'haiku'},
            acceptance={'browser': {'base_url': BASE}, 'journeys': {'enabled': True, 'start_command': 'npm start'}})
    agent, builds = clicker([{}]), []
    assert ar.run(project, max_iterations=2, claude=fake_agent(calls=builds), journey_hooks=hooks(agent),
                  allow_dirty=True) == 0
    assert all(c[c.index('--model') + 1] == 'sonnet' for c in builds)
    assert agent.calls and all(c[c.index('--model') + 1] == 'haiku' for c in agent.calls)
