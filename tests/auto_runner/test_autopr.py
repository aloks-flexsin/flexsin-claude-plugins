"""The pull request description (bin/autopr.py, central task #25): the team template filled from the run's facts."""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parents[2] / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autopr_t', BIN / 'autopr.py')
ap = importlib.util.module_from_spec(spec)
sys.modules['autopr_t'] = ap
spec.loader.exec_module(ap)

HEADINGS = ['## 1. Summary', '## 2. Problem Statement', '## 3. Proposed Changes', '## 4. Technical Implementation',
            '## 5. Testing & Validation', '## 6. Impact Assessment', '## 7. Screenshots / Evidence',
            '## 8. Known Limitations / Pending Work', '## 9. Reviewer Checklist', '## 10. Deployment Notes',
            '**AI Agent Self-Assessment**']


def item(i, done=True, state=None, kind='feature', change=''):
    return {'id': i, 'title': f'Thing {i}', 'done': done, 'state': state or ('done' if done else 'queued'),
            'detail': '', 'kind': kind, 'acceptance': 'it works', 'change': change}


def facts(**kw):
    base = {'title': 'CSV export', 'problem': 'People cannot export.', 'task': 7, 'branch': 'u/a-claude-auto-runner/feat/csv',
            'base': 'main', 'author': 'u/a-claude-auto-runner (AI agent)', 'items': [item('A1')], 'commits': ['abc A1'],
            'files': [{'status': 'M', 'path': 'app/export.py'}], 'tests': [{'item': 'A1', 'scope': 'full', 'result': 'pass'}],
            'checks': [], 'evidence': [], 'suite': '120 passed in 3s'}
    base.update(kw)
    return base


def boxes(text):
    return {m.group(2): m.group(1) == 'x' for m in re.finditer(r'^- \[( |x)\] (.+)$', text, re.M)}


def assessment(text, field):
    return re.search(r'^- ' + re.escape(field) + r': (.*)$', text, re.M).group(1)


# ---------- the description (Z2) ----------

def test_header_and_every_heading_once_in_order():
    text = ap.pr_body(facts())
    assert text.startswith('# Pull Request: CSV export\n')
    for line in ('**PR ID:** assigned by GitHub on creation', '**Author:** u/a-claude-auto-runner (AI agent)',
                 '**Branch:** `u/a-claude-auto-runner/feat/csv` → `main`', '**Linked Issue:** central task #7',
                 '**Status:** Ready for Review'):
        assert line in text
    positions = [text.index(h) for h in HEADINGS]
    assert positions == sorted(positions) and all(text.count(h) == 1 for h in HEADINGS)
    assert '**Status:** Draft' in ap.pr_body(facts(), draft=True)


def test_every_label_of_the_template_appears():
    template = ap.TEMPLATE.read_text(encoding='utf-8')
    text = ap.pr_body(facts())
    for label in re.findall(r'^- \[ \] (.+)$', template, re.M):
        assert label in boxes(text)
    for field in re.findall(r'^- ([A-Z][a-z ]+): \[', template, re.M):
        assert f'- {field}: ' in text
    for heading in re.findall(r'^## .+$', template, re.M):
        assert heading in text


def test_empty_facts_say_none_and_tick_nothing():
    text = ap.pr_body({'branch': 'b', 'base': 'main'})
    assert ap.NONE in text and not any(boxes(text).values())
    assert assessment(text, 'Test confidence') == 'Low' and assessment(text, 'Recommended action') == 'Needs Human Review'
    assert 'No independent test run recorded.' in text


def test_a_green_full_run_ticks_the_test_boxes_and_gives_high():
    text = ap.pr_body(facts())
    ticked = {k for k, v in boxes(text).items() if v}
    assert ticked == {'Unit tests passed', 'Existing regression tests passed'}
    assert assessment(text, 'Test confidence') == 'High' and assessment(text, 'Implementation confidence') == 'High'
    assert assessment(text, 'Recommended action') == 'Approve' and '**Test Results:** 120 passed in 3s' in text


def test_test_confidence_rules_in_order():
    item_only = [{'item': 'A1', 'scope': 'item', 'result': 'pass'}, {'item': 'A2', 'scope': 'item', 'result': 'pass'}]
    assert ap.test_confidence(item_only) == 'Medium'
    assert ap.test_confidence(item_only + [{'item': 'A2', 'scope': 'full', 'result': 'fail'}]) == 'Low'
    assert ap.test_confidence([{'item': 'A1', 'scope': 'item', 'result': 'fail'}]) == 'Low'
    assert ap.test_confidence([]) == 'Low'
    text = ap.pr_body(facts(tests=item_only))
    assert not boxes(text)['Unit tests passed'] and assessment(text, 'Recommended action') == 'Needs Human Review'


def test_implementation_confidence_rules_in_order():
    assert ap.implementation_confidence([item('A1'), item('A2')]) == 'High'
    assert ap.implementation_confidence([item('A1'), item('A2', done=False)]) == 'Medium'
    assert ap.implementation_confidence([item('A1', done=False), item('A2', done=False, state='blocked')]) == 'Low'
    text = ap.pr_body(facts(items=[item('A1'), item('A2', done=False, state='blocked')]))
    assert 'A2 Thing A2: blocked' in text.split('## 8.')[1].split('## 9.')[0]


def test_migrations_dependencies_configs_and_breaking_changes():
    files = [{'status': 'M', 'path': 'auto-runner/central/schema.sql'}, {'status': 'A', 'path': 'web/package.json'},
             {'status': 'M', 'path': '.claude/auto.json'}]
    text = ap.pr_body(facts(files=files, items=[item('A1', kind='breaking')]))
    deploy = text.split('## 10. Deployment Notes')[1]
    assert 'Migration requirements: auto-runner/central/schema.sql' in deploy
    assert 'Environment/configuration changes: .claude/auto.json' in deploy
    assert 'Dependencies added/updated: web/package.json' in text
    assert 'Potential breaking changes: A1 Thing A1' in text
    assert 'Affected modules/services: .claude, auto-runner, web' in text


# ---------- the facts (Z1) ----------

def sh(cwd, *args):
    subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True)


def commit(p, msg):
    sh(p, 'add', '-A')
    sh(p, '-c', 'user.email=t@example.com', '-c', 'user.name=T', 'commit', '-q', '-m', msg)


QUEUE = '# Queue\n\n- [x] **A0 Old.** old\n  *Acceptance:* old\n\n## Needs a person\n'
ITEMS = ('- [x] **B1 First part.** x\n  *Acceptance:* first works\n  *Kind:* feature\n'
         '- [ ] **B2 Second part.** x\n  *Acceptance:* second works\n  *Status:* blocked which colour?\n'
         '- [ ] **B3 Third part.** x\n  *Acceptance:* third works\n')
PLAN = '# CSV export for reports\n\n## Intent\nPeople need the table as CSV.\n\n## Slices\n1. x\n'


def test_gather_reads_plan_items_commits_files_ledger_and_changes(tmp_path):
    p = tmp_path / 'p'
    (p / '.auto').mkdir(parents=True)
    sh(p, 'init', '-q', '-b', 'main')
    (p / '.auto' / 'queue.md').write_text(QUEUE, encoding='utf-8')
    commit(p, 'base')
    sh(p, 'switch', '-q', '-c', 'u/a-claude-auto-runner/feat/csv')
    (p / '.auto' / 'queue.md').write_text(QUEUE.replace('## Needs', ITEMS + '\n## Needs'), encoding='utf-8')
    (p / 'docs' / 'plans').mkdir(parents=True)
    (p / 'docs' / 'plans' / 'task-7-csv.md').write_text(PLAN, encoding='utf-8')
    commit(p, 'Queue: task-7-csv')
    (p / 'export.py').write_text('x', encoding='utf-8')
    commit(p, 'B1 export')
    (p / '.auto' / 'changes').mkdir()
    (p / '.auto' / 'changes' / 'B1.txt').write_text('You can export the table as CSV.\n', encoding='utf-8')
    events = [{'type': 'tests', 'item': 'B1', 'detail': {'scope': 'item', 'result': 'fail'}},
              {'type': 'tests', 'item': 'B1', 'detail': {'scope': 'item', 'result': 'pass'}},
              {'type': 'tests', 'item': 'B1', 'detail': {'scope': 'full', 'result': 'pass'}},
              {'type': 'tests', 'item': 'Z9', 'detail': {'scope': 'full', 'result': 'fail'}},      # another run's item
              {'type': 'browser', 'item': 'B1', 'detail': {'result': 'pass', 'evidence': '.auto/evidence/B1/browser'}}]
    (p / '.auto' / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events) + '\n', encoding='utf-8')
    f = ap.gather(p, p, {}, 'main', 'u/a-claude-auto-runner/feat/csv', task=7, name='u/a-claude-auto-runner',
                  suite='3 passed')
    assert f['title'] == 'CSV export for reports' and f['problem'] == 'People need the table as CSV.'
    assert f['plan'] == 'docs/plans/task-7-csv.md' and f['task'] == 7
    assert [(i['id'], i['done'], i['state']) for i in f['items']] == [('B1', True, 'done'), ('B2', False, 'blocked'),
                                                                     ('B3', False, 'queued')]
    assert f['items'][0]['change'] == 'You can export the table as CSV.' and f['items'][1]['acceptance'] == 'second works'
    assert len(f['commits']) == 2 and {x['path'] for x in f['files']} == {'.auto/queue.md', 'docs/plans/task-7-csv.md', 'export.py'}
    assert sorted((t['scope'], t['result']) for t in f['tests']) == [('full', 'pass'), ('item', 'pass')]
    assert f['evidence'] == ['.auto/evidence/B1/browser'] and f['author'] == 'u/a-claude-auto-runner (AI agent)'
    text = ap.pr_body(f)
    assert assessment(text, 'Implementation confidence') == 'Low' and assessment(text, 'Test confidence') == 'High'


def test_gather_without_a_plan_uses_the_branch_brief(tmp_path):
    p = tmp_path / 'p'
    p.mkdir()
    sh(p, 'init', '-q', '-b', 'main')
    (p / 'a.txt').write_text('a', encoding='utf-8')
    commit(p, 'base')
    sh(p, 'switch', '-q', '-c', 'u/a-claude-auto-runner/fix/csv-quotes')
    (p / 'a.txt').write_text('b', encoding='utf-8')
    commit(p, 'fix')
    f = ap.gather(p, p, {}, 'main', 'u/a-claude-auto-runner/fix/csv-quotes', task=None, name='')
    assert f['title'] == 'csv quotes' and f['problem'] == '' and f['items'] == []
    assert ap.NONE in ap.pr_body(f).split('## 2. Problem Statement')[1].split('## 3.')[0]
