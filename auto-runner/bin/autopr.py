"""The description of every pull request the runner opens (central task #25): the team's template in
`docs/pr-template.md`, filled from facts the run already has. Nothing in it is invented: a part with nothing known says
"None recorded by this run.", and a box is ticked only from evidence. Stdlib only.

`gather(project, wt, cfg, base, branch, start, suite=None)` collects the facts:
- the request's plan: the `docs/plans/*.md` file added on the branch (`git diff --name-only --diff-filter=A
  start...branch -- docs/plans/`), which `auto_run.apply_pending` commits with the queue items of every request; the
  title is its first `# ` heading (else the branch's brief) and the problem statement its "## Intent" section;
- the central task number (`autotasks.current`), when the run works on one;
- the run's items: those in the worktree queue that the base did not have, with tick, kind, acceptance line and status
  line (`queue.parse_items`, `queue.parse_status`, `queue.parse_kind`) and the change sentence from
  `.auto/changes/<item>.txt`;
- commits (`git log start..branch`) and changed files (`git diff --name-status start...branch`);
- the run's independent test runs (the ledger's `tests` events for those items: full and item-level), `check` events
  and browser evidence paths (`browser` events);
- `suite`, the "suite at the last independent run" line the runner printed.

`pr_body(facts, draft)` writes the template's header and sections 1 to 10 and the AI Agent Self-Assessment in order
(see the plan `docs/plans/task-25-pr-description-rules-fro-autor.md`, "How each part is filled").
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / 'docs' / 'pr-template.md'
NONE = 'None recorded by this run.'
NOT_ASSESSED = 'Not assessed by the runner: reviewer to judge.'
MIGRATIONS = re.compile(r'(^|/)(migrations?/|schema\.sql$|db\.py$)')
MANIFESTS = re.compile(r'(^|/)(package\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|pyproject\.toml|poetry\.lock|'
                       r'requirements[^/]*\.txt|Pipfile(\.lock)?|go\.mod|go\.sum|Cargo\.toml|Cargo\.lock)$')
CONFIGS = re.compile(r'(^|/)(\.claude/auto\.json|config\.schema\.json|\.env\.example)$')
TESTS_BOXES = ('Unit tests passed', 'Existing regression tests passed')
SECTION5 = ('Unit tests passed', 'Integration tests passed', 'Existing regression tests passed', 'Build successful',
            'Linting/type checks passed', 'Edge cases validated')
SECTION9 = ('Requirements satisfied', 'Code quality and maintainability verified', 'No unnecessary code or dependencies',
            'Security considerations reviewed', 'Tests provide sufficient coverage', 'No unrelated changes included')


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


queue = _load('autorunner_queue_pr', 'queue.py')


def _git(cwd, *args):
    r = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    return r.stdout.strip() if r.returncode == 0 else ''


def section(text, heading):
    """The body of `## heading` in markdown `text` (up to the next `## `), stripped, or ''."""
    m = re.search(r'^## ' + re.escape(heading) + r'\s*\n(.*?)(?=^## |\Z)', text or '', re.M | re.S)
    return m.group(1).strip() if m else ''


def brief(branch):
    """The last part of a branch name as words: `.../feat/csv-export` -> `csv export`."""
    return branch.rstrip('/').split('/')[-1].replace('-', ' ').replace('_', ' ')


def plan_of(wt, start, branch):
    """-> (path, text) of the request's plan added on the branch, or (None, '')."""
    added = _git(wt, 'diff', '--name-only', '--diff-filter=A', f'{start}...{branch}', '--', 'docs/plans/')
    for rel in added.splitlines():
        if rel.endswith('.md'):
            path = Path(wt) / rel
            if path.is_file():
                return rel, path.read_text(encoding='utf-8')
    return None, ''


def run_items(wt, cfg, start):
    """The run's items: the worktree queue's items that the queue at `start` did not have, in file order."""
    rel = cfg.get('queue_file') or '.auto/queue.md'
    path = Path(wt) / rel
    now = path.read_text(encoding='utf-8') if path.exists() else ''
    before = _git(wt, 'show', f'{start}:{rel}')
    old = {i for i, _, _ in queue.parse_items(before)}
    out = []
    for item_id, checked, block in queue.parse_items(now):
        if item_id in old:
            continue
        title = re.match(r'^- \[[ x]\] \*\*[A-Z]{1,3}\d+\s+(.*?)\.?\*\*', block)
        acc = re.search(r'^\s*\*Acceptance:\*\s*(.*)$', block, re.M)
        state, detail = queue.parse_status(block, checked)
        change = Path(wt) / '.auto' / 'changes' / f'{item_id}.txt'
        try:
            kind = queue.parse_kind(block)
        except ValueError:
            kind = 'internal'
        out.append({'id': item_id, 'title': title.group(1) if title else '', 'done': checked, 'state': state,
                    'detail': detail, 'kind': kind, 'acceptance': acc.group(1) if acc else '',
                    'change': change.read_text(encoding='utf-8').strip() if change.is_file() else ''})
    return out


def ledger_facts(project, ids):
    """-> (tests, checks, evidence) from `.auto/events.jsonl` for the items `ids`: the last result per item and scope."""
    path = Path(project) / '.auto' / 'events.jsonl'
    tests, checks, evidence = {}, {}, []
    if not path.exists():
        return [], [], []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        item, detail = ev.get('item'), ev.get('detail') or {}
        if item not in ids:
            continue
        if ev.get('type') == 'tests':
            tests[(item, detail.get('scope') or 'full')] = detail.get('result')
        elif ev.get('type') == 'check':
            checks[item] = detail.get('result')
        elif ev.get('type') == 'browser' and detail.get('evidence'):
            evidence.append(detail['evidence'])
    return ([{'item': i, 'scope': s, 'result': r} for (i, s), r in tests.items()],
            [{'item': i, 'result': r} for i, r in checks.items()], evidence)


def gather(project, wt, cfg, base, branch, start=None, suite=None, task=None, name=None):
    """-> the facts `pr_body` writes from. `task` (the central task number) defaults to `autotasks.current`; `name`
    (`<user>/<account>-claude-auto-runner`) to `autosafe.runner_name()`."""
    start = start or base
    if task is None:
        try:
            current = _load('autorunner_tasks', 'autotasks.py').current(Path(project) / '.auto')
            task = current['number'] if current else None
        except Exception:
            task = None
    plan_path, plan = plan_of(wt, start, branch)
    heading = re.search(r'^# (.+)$', plan, re.M)
    items = run_items(wt, cfg, start)
    tests, checks, evidence = ledger_facts(project, {i['id'] for i in items})
    files = [line.split('\t', 1) for line in _git(wt, 'diff', '--name-status', f'{start}...{branch}').splitlines()
             if '\t' in line]
    return {'title': heading.group(1).strip() if heading else brief(branch), 'plan': plan_path,
            'problem': section(plan, 'Intent')[:1500], 'task': task, 'branch': branch, 'base': base,
            'author': (name if name is not None else _load('autorunner_safe', 'autosafe.py').runner_name()
                       or '<user>/<account>-claude-auto-runner') + ' (AI agent)',
            'items': items, 'commits': _git(wt, 'log', '--oneline', '--no-decorate', f'{start}..{branch}').splitlines(),
            'files': [{'status': s, 'path': p} for s, p in files], 'tests': tests, 'checks': checks,
            'evidence': evidence, 'suite': suite}


# ---------- the description ----------

def implementation_confidence(items):
    if any(i['state'] == 'blocked' for i in items):
        return 'Low'
    if any(not i['done'] for i in items):
        return 'Medium'
    return 'High'


def test_confidence(tests):
    full = [t['result'] for t in tests if t['scope'] == 'full']
    if full and full[-1] == 'pass':
        return 'High'
    item_runs = [t['result'] for t in tests if t['scope'] == 'item']
    if not full and item_runs and all(r == 'pass' for r in item_runs):
        return 'Medium'
    return 'Low'


def _box(label, ticked):
    return f'- [{"x" if ticked else " "}] {label}'


def _list(lines):
    return '\n'.join(f'- {line}' for line in lines) if lines else NONE


def pr_body(facts, draft=False):
    """The pull request description: the template's header, sections 1 to 10 and the self-assessment, in order."""
    items, files = facts.get('items') or [], facts.get('files') or []
    paths = [f['path'] for f in files]
    tests = facts.get('tests') or []
    full_green = any(t['scope'] == 'full' for t in tests) and [t['result'] for t in tests if t['scope'] == 'full'][-1] == 'pass'
    check_green = bool(facts.get('checks')) and all(c['result'] == 'pass' for c in facts['checks'])
    done = [i for i in items if i['done']]
    pending = [i for i in items if not i['done']]
    migrations = [p for p in paths if MIGRATIONS.search(p)]
    manifests = [p for p in paths if MANIFESTS.search(p)]
    configs = [p for p in paths if CONFIGS.search(p)]
    breaking = [i for i in items if i['kind'] == 'breaking']
    modules = sorted({p.split('/', 1)[0] for p in paths})
    impl, test = implementation_confidence(items), test_confidence(tests)
    concerns = [f'{i["id"]} {i["title"]}: {i["state"]}{" — " + i["detail"] if i["detail"] else ""}' for i in pending]
    task = facts.get('task')
    lines = [
        f'# Pull Request: {facts.get("title") or NONE}',
        '',
        '**PR ID:** assigned by GitHub on creation  ',
        f'**Author:** {facts.get("author") or "auto-runner (AI agent)"}  ',
        f'**Branch:** `{facts.get("branch")}` → `{facts.get("base")}`  ',
        f'**Linked Issue:** {f"central task #{task}" if task else "none"}  ',
        f'**Status:** {"Draft" if draft else "Ready for Review"}',
        '',
        '---',
        '',
        '## 1. Summary',
        (f'{facts.get("title")}: {len(done)} of {len(items)} item(s) done in this run.' if items else NONE),
        '',
        '## 2. Problem Statement',
        facts.get('problem') or NONE,
        '',
        '## 3. Proposed Changes',
        _list([f'{i["id"]} {i["title"]}' + (f': {i["change"]}' if i['change'] else '') for i in items]),
        '',
        '## 4. Technical Implementation',
        '- Files/modules modified: ' + (', '.join(f'{f["path"]} ({f["status"]})' for f in files) or NONE),
        '- Architecture or logic changes: ' + ('See the commits:\n' + '\n'.join(f'  - {c}' for c in facts['commits'])
                                               if facts.get('commits') else NONE),
        '- APIs/database changes: ' + (', '.join(migrations) if migrations else 'None detected'),
        '- Dependencies added/updated: ' + (', '.join(manifests) if manifests else 'None detected'),
        '',
        '## 5. Testing & Validation',
        *[_box(label, (label in TESTS_BOXES and full_green) or (label == 'Build successful' and check_green))
          for label in SECTION5],
        '',
        '**Test Results:** ' + (facts.get('suite') or 'No independent test run recorded.'),
        '',
        '## 6. Impact Assessment',
        '- Potential breaking changes: ' + (', '.join(f'{i["id"]} {i["title"]}' for i in breaking) or 'None declared'),
        '- Performance implications: ' + NOT_ASSESSED,
        '- Security implications: ' + NOT_ASSESSED,
        '- Backward compatibility: ' + NOT_ASSESSED,
        '- Affected modules/services: ' + (', '.join(modules) or NONE),
        '',
        '## 7. Screenshots / Evidence',
        _list(facts.get('evidence') or []),
        '',
        '## 8. Known Limitations / Pending Work',
        _list(concerns),
        '',
        '## 9. Reviewer Checklist',
        *[_box(label, False) for label in SECTION9],
        '',
        '## 10. Deployment Notes',
        '- Environment/configuration changes: ' + (', '.join(configs) if configs else 'None detected'),
        '- Migration requirements: ' + (', '.join(migrations) if migrations else 'None detected'),
        '- Deployment considerations: ' + ('Run the migrations above before or with the deploy.' if migrations
                                           else 'None detected'),
        "- Rollback strategy: Revert this pull request's merge commit.",
        '',
        '---',
        '',
        '**AI Agent Self-Assessment**',
        '',
        f'- Implementation confidence: {impl}',
        f'- Test confidence: {test}',
        '- Unresolved concerns: ' + ('; '.join(concerns) if concerns else 'None'),
        '- Recommended action: ' + ('Approve' if impl == 'High' and test == 'High' else 'Needs Human Review'),
    ]
    return '\n'.join(lines) + '\n'
