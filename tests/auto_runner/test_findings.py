"""auto-runner plugin: Layer 2, the exploratory tester (findings contract, ingest, MCP config, explore, runner wiring) with a fake claude."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autorunner_run_fd', BIN / 'auto_run.py')
ar = importlib.util.module_from_spec(spec)
sys.modules['autorunner_run_fd'] = ar
spec.loader.exec_module(ar)
ab, queue, autoevents = ar.autobrowser, ar.queue, ar.autoevents

PY = f'"{sys.executable}"'
GREEN = f'{PY} -c "import sys; sys.exit(0)"'
BROWSER = {'enabled': True, 'base_url': 'http://127.0.0.1:5000', 'command': 'npx playwright test', 'exploratory': True}

QUEUE = """# Queue

- [ ] **A1 Sign-in page.** Build the sign-in page in a small file, with enough words to pass any length rule we might have.
  *Acceptance:* a person can sign in.
  *Browser:* journeys: sign-in; roles: admin

## Needs a person
- nothing
"""


def sh(cwd, *args):
    subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True)


def finding(**over):
    f = {'schema': 'finding.v1', 'title': 'Search box drops the last word', 'steps': ['Open /ask', 'Type "sap erp"', 'Press Enter'],
         'expected': 'Results for both words', 'actual': 'Results for "sap" only', 'severity': 'medium',
         'evidence': ['shots/search.png']}
    f.update(over)
    return f


def write_finding(new_dir, name='one.json', data=None, raw=None, shot=True):
    new_dir = Path(new_dir)
    new_dir.mkdir(parents=True, exist_ok=True)
    if shot:
        (new_dir / 'shots').mkdir(exist_ok=True)
        (new_dir / 'shots' / 'search.png').write_bytes(b'png')
    (new_dir / name).write_text(raw if raw is not None else json.dumps(data or finding()), encoding='utf-8')


def fake_explorer(findings=(), calls=None, code=0, touch=None):
    """A fake exploratory `claude`: writes the given findings where the prompt says, optionally edits a tracked file."""
    def claude(cmd, cwd):
        if calls is not None:
            calls.append(list(cmd))
        new = Path(cwd) / '.auto' / 'findings' / 'new'
        for n, f in enumerate(findings):
            write_finding(new, f'f{n}.json', f)
        if touch:
            (Path(cwd) / touch).write_text('changed', encoding='utf-8')
        return code, 'explored'
    return claude


@pytest.fixture
def wt(tmp_path):
    p = tmp_path / 'wt'
    p.mkdir()
    sh(p, 'init', '-q', '-b', 'main')
    sh(p, 'config', 'user.email', 't@example.com')
    sh(p, 'config', 'user.name', 'T')
    (p / '.gitignore').write_text('.auto/\n', encoding='utf-8')
    (p / 'app.py').write_text('x = 1\n', encoding='utf-8')
    sh(p, 'add', '.gitignore', 'app.py')
    sh(p, 'commit', '-q', '-m', 'init')
    return p


# --- findings contract and ingest ------------------------------------------------------------------------------------

def test_valid_finding_is_stored_with_a_proposed_item(tmp_path):
    new = tmp_path / 'wt' / '.auto' / 'findings' / 'new'
    write_finding(new)
    res = ab.ingest_findings(new, tmp_path / 'proj', 'A1', roles=['admin'])
    assert res['rejected'] == [] and [s['ref'] for s in res['stored']] == ['A1-F1']
    root = tmp_path / 'proj' / '.auto' / 'findings'
    stored = json.loads((root / 'A1-F1.json').read_text(encoding='utf-8'))
    assert stored['evidence'] == ['A1-F1/shots/search.png'] and (root / 'A1-F1' / 'shots' / 'search.png').is_file()
    assert not list(new.glob('*.json'))
    text = (root / 'proposed' / 'A1-F1.md').read_text(encoding='utf-8')
    for step in finding()['steps']:
        assert step in text
    assert 'A1-F1' in text and '*Acceptance:*' in text
    items = queue.parse_items(text)
    assert len(items) == 1 and queue.parse_browser(items[0][2]) == {'journeys': ['search-box-drops-the-last-word'],
                                                                    'roles': ['admin']}


def test_second_ingest_numbers_on(tmp_path):
    new = tmp_path / 'new'
    write_finding(new)
    ab.ingest_findings(new, tmp_path / 'proj', 'A1')
    write_finding(new)
    res = ab.ingest_findings(new, tmp_path / 'proj', 'A1')
    assert [s['ref'] for s in res['stored']] == ['A1-F2']


@pytest.mark.parametrize('data,raw,shot,reason', [
    (finding(severity='urgent'), None, True, 'finding.severity'),
    (finding(steps=[]), None, True, 'finding.steps'),
    ({k: v for k, v in finding().items() if k != 'actual'}, None, True, 'missing field actual'),
    (finding(extra='x'), None, True, 'unknown field extra'),
    (finding(schema='finding.v2'), None, True, 'finding.schema'),
    (finding(), None, False, 'finding.evidence'),
    (finding(evidence=['../../app.py']), None, True, 'finding.evidence'),
    (None, '{not json', True, 'not valid JSON'),
])
def test_invalid_finding_is_rejected_with_reasons(tmp_path, data, raw, shot, reason):
    new = tmp_path / 'new'
    write_finding(new, data=data, raw=raw, shot=shot)
    res = ab.ingest_findings(new, tmp_path / 'proj', 'A1')
    assert res['stored'] == [] and len(res['rejected']) == 1
    assert any(reason in e for e in res['rejected'][0]['errors'])
    rej = tmp_path / 'proj' / '.auto' / 'findings' / 'rejected'
    assert (rej / 'A1-one.json').is_file()
    assert reason in (rej / 'A1-one.json.reasons.txt').read_text(encoding='utf-8')
    assert not (new / 'one.json').exists()
    assert not list((tmp_path / 'proj' / '.auto' / 'findings').glob('A1-F*.json'))


def test_stored_finding_is_redacted(tmp_path):
    new = tmp_path / 'new'
    write_finding(new, data=finding(actual='signed in as secret-pass-123 and saw nothing'))
    ab.ingest_findings(new, tmp_path / 'proj', 'A1', secrets={'AUTO_TEST_ADMIN_PASSWORD': 'secret-pass-123'})
    texts = [p.read_text(encoding='utf-8') for p in (tmp_path / 'proj' / '.auto' / 'findings').rglob('*.json')]
    texts += [p.read_text(encoding='utf-8') for p in (tmp_path / 'proj' / '.auto' / 'findings').rglob('*.md')]
    assert texts and not any('secret-pass-123' in t for t in texts)


# --- MCP config -------------------------------------------------------------------------------------------------------

def test_mcp_config_holds_only_allowed_origins():
    cfg = ab.mcp_config('http://127.0.0.1:5000/app/', ['http://shop.test/x'])
    assert list(cfg['mcpServers']) == ['playwright']
    args = cfg['mcpServers']['playwright']['args']
    origins = args[args.index('--allowed-origins') + 1].split(';')
    assert origins == ['http://127.0.0.1:5000', 'http://shop.test']


@pytest.mark.parametrize('bad', ['https://example.com', 'http://localhost.evil.com', 'http://user@localhost', 'ftp://localhost'])
def test_mcp_config_rejects_a_non_local_origin(bad):
    with pytest.raises(ValueError, match='refused'):
        ab.mcp_config('http://localhost:5000', [bad])
    with pytest.raises(ValueError, match='refused'):
        ab.mcp_config(bad)


# --- explore ----------------------------------------------------------------------------------------------------------

def test_explore_does_not_run_when_disabled(tmp_path, wt):
    calls = []
    for browser in ({**BROWSER, 'exploratory': False}, {k: v for k, v in BROWSER.items() if k != 'exploratory'}):
        res = ab.explore(tmp_path / 'proj', wt, 'A1', browser, claude=fake_explorer([finding()], calls))
        assert res['ran'] is False and 'exploratory is off' in res['reason']
    assert calls == [] and not (wt / '.auto').exists()


def test_explore_refuses_a_non_local_base_url(tmp_path, wt):
    calls = []
    res = ab.explore(tmp_path / 'proj', wt, 'A1', {**BROWSER, 'base_url': 'https://example.com'}, claude=fake_explorer(calls=calls))
    assert res['ran'] is False and 'refused' in res['reason'] and calls == []


def test_explore_stores_findings_and_limits_the_agent(tmp_path, wt):
    calls = []
    proj = tmp_path / 'proj'
    res = ab.explore(proj, wt, 'A1', BROWSER, {'journeys': ['sign-in'], 'roles': ['admin']},
                     claude=fake_explorer([finding(), finding(severity='bogus')], calls))
    assert res['ran'] and res['errors'] == []
    assert [s['ref'] for s in res['stored']] == ['A1-F1'] and len(res['rejected']) == 1
    cmd = calls[0]
    assert cmd[:2] == ['claude', '-p'] and '--strict-mcp-config' in cmd
    prompt = cmd[2]
    assert 'data' in prompt and 'never an instruction' in prompt and '.auto/findings/new/' in prompt
    assert '--permission-mode' not in cmd                     # never acceptEdits: writes need the allow rule below
    allowed = cmd[cmd.index('--allowedTools') + 1:cmd.index('--disallowedTools')]
    assert allowed == ['mcp__playwright', 'Write(.auto/findings/new/**)']
    assert {'Bash', 'Edit', 'WebFetch'} <= set(cmd[cmd.index('--disallowedTools') + 1:])
    mcp = json.loads(Path(cmd[cmd.index('--mcp-config') + 1]).read_text(encoding='utf-8'))
    assert list(mcp['mcpServers']) == ['playwright']


def test_explore_reports_a_change_outside_the_findings_folder(tmp_path, wt):
    res = ab.explore(tmp_path / 'proj', wt, 'A1', BROWSER, claude=fake_explorer([finding()], touch='app.py'))
    assert any('outside the findings folder' in e for e in res['errors'])


# --- runner wiring ------------------------------------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(ar.autosafe, '_doctor', lambda p: [])
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


def fake_browser(status='pass'):
    def runner(command, cwd, env):
        res = Path(env['AUTO_BROWSER_RESULTS'])
        res.parent.mkdir(parents=True, exist_ok=True)
        res.write_text(json.dumps({'schema': 'browser.v1', 'item': env['AUTO_ITEM'], 'criteria': [
            {'id': 'sign-in', 'status': status, 'duration_s': 0.1,
             'artifacts': {'screenshot': None, 'trace': None, 'console': None, 'network': None}}]}), encoding='utf-8')
        return 0, 'ok'
    return runner


def agent_and_explorer(explore_calls):
    explorer = fake_explorer([finding(severity='critical')], explore_calls)

    def claude(cmd, cwd):
        if '--mcp-config' in cmd:
            return explorer(cmd, cwd)
        cwd = Path(cwd)
        q = cwd / '.auto' / 'queue.md'
        q.write_text(q.read_text(encoding='utf-8').replace('- [ ] **A1', '- [x] **A1', 1), encoding='utf-8')
        (cwd / 'A1.txt').write_text('made', encoding='utf-8')
        sh(cwd, 'add', 'A1.txt')
        sh(cwd, '-c', 'user.email=t@example.com', '-c', 'user.name=T', 'commit', '-q', '-m', 'A1')
        return 0, 'done'
    return claude


def runs(p):
    return [json.loads(x) for x in (p / '.auto' / 'runs.jsonl').read_text(encoding='utf-8').splitlines()]


def test_runner_findings_do_not_override_a_passing_layer1(project):
    calls = []
    code = ar.run(project, max_iterations=1, claude=agent_and_explorer(calls), browser_runner=fake_browser())
    assert code == 0 and len(calls) == 1
    r = runs(project)[0]
    assert r['result'] == 'done' and r['browser']['result'] == 'pass' and r['findings'] == ['A1-F1']
    st = {i: s for i, s, _ in queue.statuses((project / '.auto' / 'queue.md').read_text(encoding='utf-8'))}
    assert st['A1'] == 'done'
    assert (project / '.auto' / 'findings' / 'proposed' / 'A1-F1.md').is_file()
    ev = [e for e in autoevents.read(project / '.auto') if e['type'] == 'findings']
    assert len(ev) == 1 and ev[0]['detail']['stored'] == ['A1-F1'] and ev[0]['evidence_class'] == 'agent_reported'
    log = next((project / '.auto' / 'logs').glob('*-iter1.log')).read_text(encoding='utf-8')
    assert 'exploratory findings (advice only)' in log and 'A1-F1 (critical)' in log


def test_runner_failing_layer1_runs_no_exploration(project):
    calls = []
    code = ar.run(project, max_iterations=1, claude=agent_and_explorer(calls), browser_runner=fake_browser('fail'))
    assert code == 1 and calls == [] and runs(project)[0]['result'] == 'red'
    assert not [e for e in autoevents.read(project / '.auto') if e['type'] == 'findings']


def test_runner_exploratory_off_runs_no_exploration(project):
    cfg = project / '.claude' / 'auto.json'
    cfg.write_text(json.dumps({'test_command': GREEN, 'acceptance': {'browser': {**BROWSER, 'exploratory': False}}}),
                   encoding='utf-8')
    sh(project, 'commit', '-q', '-am', 'off')
    calls = []
    code = ar.run(project, max_iterations=1, claude=agent_and_explorer(calls), browser_runner=fake_browser())
    assert code == 0 and calls == [] and 'findings' not in runs(project)[0]
