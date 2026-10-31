"""auto-runner plugin: the Layer 1 browser stage (results contract, run_stage, queue grammar, runner wiring) with a fake browser."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autorunner_run_bs', BIN / 'auto_run.py')
ar = importlib.util.module_from_spec(spec)
sys.modules['autorunner_run_bs'] = ar
spec.loader.exec_module(ar)
ab, queue, autoevents = ar.autobrowser, ar.queue, ar.autoevents

PY = f'"{sys.executable}"'
GREEN = f'{PY} -c "import sys; sys.exit(0)"'
BROWSER = {'enabled': True, 'base_url': 'http://127.0.0.1:5000', 'command': 'npx playwright test'}

QUEUE = """# Queue

- [ ] **A1 Sign-in page.** Build the sign-in page in a small file, with enough words to pass any length rule we might have.
  *Acceptance:* a person can sign in.
  *Browser:* journeys: sign-in, sign-out; roles: admin

- [ ] **A2 Plain thing.** Build the second thing in a small file, with enough words to pass any length rule we might have.
  *Acceptance:* the file exists.

## Needs a person
- nothing
"""


def sh(cwd, *args):
    subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True)


def criterion(cid, status='pass', screenshot=None):
    return {'id': cid, 'status': status, 'duration_s': 0.5,
            'artifacts': {'screenshot': screenshot, 'trace': None, 'console': None, 'network': None}}


def fake_browser(statuses=None, calls=None, write=True, raw=None, code=0, item=None):
    """Writes a browser.v1 file where the runner asked for it, plus one screenshot per criterion."""
    statuses = statuses or {'sign-in': 'pass', 'sign-out': 'pass'}

    def runner(command, cwd, env):
        if calls is not None:
            calls.append((command, Path(cwd), dict(env)))
        if write:
            res = Path(env['AUTO_BROWSER_RESULTS'])
            res.parent.mkdir(parents=True, exist_ok=True)
            if raw is not None:
                res.write_text(raw, encoding='utf-8')
            else:
                crit = []
                for cid, st in statuses.items():
                    (res.parent / 'shots').mkdir(exist_ok=True)
                    (res.parent / 'shots' / f'{cid}.png').write_bytes(b'png')
                    crit.append(criterion(cid, st, f'shots/{cid}.png'))
                res.write_text(json.dumps({'schema': 'browser.v1', 'item': item or env['AUTO_ITEM'], 'criteria': crit}),
                               encoding='utf-8')
        return code, 'browser output'
    return runner


@pytest.fixture
def wt(tmp_path):
    """A git folder where .auto/ is ignored (provisioning refuses otherwise)."""
    p = tmp_path / 'wt'
    p.mkdir()
    sh(p, 'init', '-q', '-b', 'main')
    (p / '.gitignore').write_text('.auto/\n', encoding='utf-8')
    return p


# --- queue grammar ----------------------------------------------------------------------------------------------------

def test_parse_browser_line():
    blocks = {i: b for i, _, b in queue.parse_items(QUEUE)}
    assert queue.parse_browser(blocks['A1']) == {'journeys': ['sign-in', 'sign-out'], 'roles': ['admin']}
    assert queue.parse_browser(blocks['A2']) is None
    assert queue.parse_browser('  *Browser:* roles: viewer') == {'journeys': [], 'roles': ['viewer']}
    with pytest.raises(ValueError, match='journys'):
        queue.parse_browser('  *Browser:* journys: x')


def test_validate_rejects_a_bad_browser_line():
    item = ('- [ ] **B1 Thing.** Build a thing with enough words to pass the length rule for new items in the queue.\n'
            '  *Acceptance:* it works.\n  *Browser:* people: admin\n')
    _, errors = queue.validate(QUEUE, item)
    assert any('B1' in e and 'people' in e for e in errors)


# --- results contract -------------------------------------------------------------------------------------------------

def test_validator_fails_closed(tmp_path):
    (tmp_path / 'a.png').write_bytes(b'x')
    good = {'schema': 'browser.v1', 'item': 'A1', 'criteria': [criterion('c1', 'pass', 'a.png')]}
    assert ab.validate_results(good, 'A1', tmp_path) == []
    bad = [
        ('not a dict', []),
        ({'schema': 'browser.v1', 'item': 'A1'}, 'missing field criteria'),
        ({**good, 'extra': 1}, 'unknown field extra'),
        ({**good, 'schema': 'browser.v0'}, 'schema'),
        ({**good, 'item': 'A2'}, 'results.item'),
        ({**good, 'criteria': []}, 'non-empty'),
        ({**good, 'criteria': [criterion('c1', 'maybe')]}, 'status'),
        ({**good, 'criteria': [criterion('c1'), criterion('c1')]}, 'appears twice'),
        ({**good, 'criteria': [{**criterion('c1'), 'duration_s': -1}]}, 'duration_s'),
        ({**good, 'criteria': [criterion('c1', 'pass', 'missing.png')]}, 'screenshot'),
        ({**good, 'criteria': [criterion('c1', 'pass', '../a.png')]}, 'screenshot'),
        ({**good, 'criteria': [{**criterion('c1'), 'artifacts': {'screenshot': None}}]}, 'artifacts'),
    ]
    for data, word in bad:
        errors = ab.validate_results(data, 'A1', tmp_path)
        assert errors, data
        assert not word or any(word in e for e in errors), (word, errors)


def test_evidence_class_local_and_hosted():
    assert ab.evidence_class('http://localhost:3000') == 'E1'
    assert ab.evidence_class('http://shop.test') == 'E1'
    assert ab.evidence_class('https://app.staging.example.com') == 'E3'


# --- run_stage ----------------------------------------------------------------------------------------------------------

def test_stage_pass_copies_evidence_and_gives_accounts(tmp_path, wt):
    project = tmp_path / 'proj'
    calls = []
    st = ab.run_stage(project, wt, 'A1', BROWSER, {'journeys': ['sign-in'], 'roles': ['admin']}, fake_browser(calls=calls))
    assert st['status'] == 'pass' and st['evidence'] == 'E1' and st['failed'] == []
    assert st['counts'] == {'pass': 2, 'fail': 0, 'skip': 0}
    command, cwd, env = calls[0]
    assert command == BROWSER['command'] and cwd == wt
    assert env['AUTO_BASE_URL'] == BROWSER['base_url'] and env['AUTO_BROWSER_JOURNEYS'] == 'sign-in'
    assert env['AUTO_TEST_ADMIN_EMAIL'].endswith('@auto-runner.test') and env['AUTO_TEST_ADMIN_PASSWORD']
    dest = project / '.auto' / 'evidence' / 'A1' / 'browser'
    assert (dest / 'results.json').exists() and (dest / 'shots' / 'sign-in.png').exists()


def test_stage_hosted_test_host_is_e3(wt):
    st = ab.run_stage(wt, wt, 'A1', {**BROWSER, 'base_url': 'https://app.staging.example.com'}, {}, fake_browser())
    assert st['status'] == 'pass' and st['evidence'] == 'E3'


def test_stage_failed_criterion_is_named(wt):
    st = ab.run_stage(wt, wt, 'A1', BROWSER, {}, fake_browser({'sign-in': 'pass', 'sign-out': 'fail'}))
    assert st['status'] == 'fail' and st['failed'] == ['sign-out']


@pytest.mark.parametrize('kwargs', [{'write': False}, {'raw': '{not json'}, {'raw': json.dumps({'schema': 'browser.v1'})},
                                    {'item': 'A9'}])
def test_stage_missing_or_invalid_file_fails_closed(wt, kwargs):
    st = ab.run_stage(wt, wt, 'A1', BROWSER, {}, fake_browser(**kwargs))
    assert st['status'] == 'invalid' and st['errors']


def test_stage_old_results_file_never_passes(wt):
    ab.run_stage(wt, wt, 'A1', BROWSER, {}, fake_browser())                 # leaves a passing file behind
    st = ab.run_stage(wt, wt, 'A1', BROWSER, {}, fake_browser(write=False))
    assert st['status'] == 'invalid'


def test_stage_nonzero_exit_or_all_skipped_fails(wt):
    assert ab.run_stage(wt, wt, 'A1', BROWSER, {}, fake_browser(code=1))['status'] == 'fail'
    assert ab.run_stage(wt, wt, 'A1', BROWSER, {}, fake_browser({'x': 'skip'}))['status'] == 'fail'


def test_stage_refused_host_runs_nothing(wt):
    calls = []
    st = ab.run_stage(wt, wt, 'A1', {**BROWSER, 'base_url': 'https://example.com'}, {'roles': ['admin']},
                      fake_browser(calls=calls))
    assert st['status'] == 'invalid' and 'base_url' in st['errors'][0] and calls == []
    assert not (wt / ab.ACCOUNTS_FILE).exists()


# --- runner wiring ------------------------------------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(ar.autosafe, '_doctor', lambda p: [])               # the capability doctor is Q1's concern
    p = tmp_path / 'proj'
    p.mkdir()
    sh(p, 'init', '-q', '-b', 'main')
    sh(p, 'config', 'user.email', 't@example.com')
    sh(p, 'config', 'user.name', 'T')
    (p / '.gitignore').write_text('.auto/\n', encoding='utf-8')
    (p / '.claude').mkdir()
    (p / '.claude' / 'auto.json').write_text(json.dumps({'test_command': GREEN, 'acceptance': {'browser': BROWSER}}),
                                            encoding='utf-8')
    (p / '.claude' / 'settings.json').write_text(
        json.dumps({'permissions': {'deny': ar.autosafe.required_rules('main')}}), encoding='utf-8')
    (p / '.auto').mkdir()
    (p / '.auto' / 'queue.md').write_text(QUEUE, encoding='utf-8')
    sh(p, 'add', '.gitignore', '.claude/auto.json', '.claude/settings.json')
    sh(p, 'commit', '-q', '-m', 'init')
    return p


def agent(cmd, cwd):
    """Ticks the item the prompt names, writes a file, commits."""
    cwd = Path(cwd)
    q = cwd / '.auto' / 'queue.md'
    text = q.read_text(encoding='utf-8')
    item = next(i for i, c, _ in queue.parse_items(text) if not c and f'item {i}' in cmd[2])
    q.write_text(text.replace(f'- [ ] **{item}', f'- [x] **{item}', 1), encoding='utf-8')
    (cwd / f'{item}.txt').write_text('made', encoding='utf-8')
    sh(cwd, 'add', f'{item}.txt')
    sh(cwd, '-c', 'user.email=t@example.com', '-c', 'user.name=T', 'commit', '-q', '-m', item)
    return 0, 'done'


def runs(p):
    return [json.loads(x) for x in (p / '.auto' / 'runs.jsonl').read_text(encoding='utf-8').splitlines()]


def browser_events(p):
    return [e for e in autoevents.read(p / '.auto') if e['type'] == 'browser']


def test_runner_passing_stage_completes_with_e1(project):
    code = ar.run(project, max_iterations=1, claude=agent, browser_runner=fake_browser())
    assert code == 0
    r = runs(project)[0]
    assert r['result'] == 'done' and r['browser'] == {'result': 'pass', 'evidence': 'E1', 'failed': []}
    st = {i: (s, d) for i, s, d in queue.statuses((project / '.auto' / 'queue.md').read_text(encoding='utf-8'))}
    assert st['A1'][0] == 'done' and '(browser E1)' in st['A1'][1]
    ev = browser_events(project)
    assert len(ev) == 1 and ev[0]['detail']['result'] == 'pass' and ev[0]['evidence_class'] == 'independent_test'
    assert (project / '.auto' / 'evidence' / 'A1' / 'browser' / 'results.json').exists()


def test_runner_failing_criterion_stops_as_red_and_names_it(project, capsys):
    calls = []

    def claude(cmd, cwd):
        calls.append(cmd)
        return agent(cmd, cwd)
    code = ar.run(project, max_iterations=3, claude=claude,
                  browser_runner=fake_browser({'sign-in': 'pass', 'sign-out': 'fail'}))
    assert code == 1 and len(calls) == 1
    r = runs(project)[0]
    assert r['result'] == 'red' and r['browser']['failed'] == ['sign-out']
    assert 'sign-out' in capsys.readouterr().out
    st = {i: (s, d) for i, s, d in queue.statuses((project / '.auto' / 'queue.md').read_text(encoding='utf-8'))}
    assert st['A1'][0] == 'blocked' and 'sign-out' in st['A1'][1]
    assert [e['detail']['result'] for e in browser_events(project)] == ['fail']
    end = [e for e in autoevents.read(project / '.auto') if e['type'] == 'run_end'][-1]
    assert end['detail']['reason'] == 'red'


def test_runner_missing_results_file_is_red(project):
    code = ar.run(project, max_iterations=1, claude=agent, browser_runner=fake_browser(write=False))
    assert code == 1 and runs(project)[0]['result'] == 'red'
    assert runs(project)[0]['browser']['result'] == 'invalid'
    log = next((project / '.auto' / 'logs').glob('*-iter1.log')).read_text(encoding='utf-8')
    assert '--- browser stage ---' in log and 'no results file' in log


def test_runner_item_without_browser_line_runs_no_stage(project):
    q = project / '.auto' / 'queue.md'
    q.write_text(QUEUE.replace('- [ ] **A1', '- [x] **A1'), encoding='utf-8')
    calls = []
    code = ar.run(project, max_iterations=1, claude=agent, browser_runner=fake_browser(calls=calls))
    assert code == 0 and calls == [] and browser_events(project) == []
    assert runs(project)[0]['result'] == 'done' and 'browser' not in runs(project)[0]


def test_runner_credentials_never_reach_log_or_events(project):
    ar.run(project, max_iterations=1, claude=agent, browser_runner=fake_browser())
    wt = project.parent / (project.name + '-auto')
    values = list(ab.known_secrets(wt).values())
    assert values
    texts = [p.read_text(encoding='utf-8') for p in (project / '.auto').rglob('*') if p.is_file()
             and p.suffix in ('.log', '.jsonl', '.md')]
    assert not any(v in t for v in values for t in texts)
