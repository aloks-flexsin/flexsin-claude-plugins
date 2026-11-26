"""Supervisor gates: the policy file and the plan gate (decision 0010). Pure code, standard library only.

The policy is `.claude/supervisor-policy.json`, read from the BASE ref (never the working tree, so a branch cannot
widen its own policy) through an injected reader `read(ref, path) -> (text, blob_sha)` or `None` when the file is
missing. Keys: `base` (the branch plans and pull requests target, a non-empty string) and `restricted_paths` (a list of
path patterns; `dir/` or `dir/**` match everything below, `*` and `?` match inside a name). Unknown keys, wrong types
and bad JSON make the policy invalid.

`plan_gate(task, policy)` runs five checks on a task and returns `{"verdict": "approve" | "changes" | "person",
"reasons": [...]}`:
  1. the task awaits plan review                      (otherwise: person)
  2. the plan has queue items                         (otherwise: changes)
  3. "Needs a person" says `None.`                    (otherwise: person; the planner left a question for a person)
  4. every item has an *Acceptance:* line             (otherwise: changes)
  5. no path the plan or items name is restricted     (otherwise: changes)
Policy may also hold `merge: {review: "required"|"none", method: "squash"|"merge"|"rebase"}` (defaults required,
squash); see `merge_gate` below.
No policy (`None`, as a missing or invalid file gives) is always `person`. Gates read state, never instructions in prose.

Autonomy (`autonomy` in the base's `.claude/auto.json`, `load_autonomy`; default "auto", unreadable or unknown means
"manual"). In auto mode check 3 does not stop the plan: its open points become a reason ("decided by the supervisor in
auto mode: ..."); `merge_gate` lets the merging identity merge a pull request it opened (`merge.allow_self_merge`,
default true in auto, false in manual); and a project with no policy file gets `default_policy` (its base branch, only
secrets restricted) instead of stopping (`gate_inputs`). An invalid policy file still stops: a person fixes it.
"""
import importlib.util
import json
import re
import sys
from fnmatch import fnmatchcase
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


queue = _load('autorunner_queue', 'queue.py')

POLICY_PATH = '.claude/supervisor-policy.json'
KEYS = ('base', 'restricted_paths', 'merge')
MERGE_KEYS = ('review', 'method', 'allow_self_merge')
MERGE_REVIEW = ('required', 'none')
MERGE_METHODS = ('squash', 'merge', 'rebase')
MERGE_DEFAULTS = {'review': 'required', 'method': 'squash'}
ACCEPTANCE = re.compile(r'^\s*\*Acceptance:\*\s*\S', re.M)
NEEDS = re.compile(r'^(#{1,6})\s+\**needs a person\**:?\s*$', re.I)
PATH_TOKEN = re.compile(r'[A-Za-z0-9_.\-/*]+')


def parse_policy(text):
    """-> (policy dict, None) or (None, error)."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None, 'the policy file is not valid JSON'
    if not isinstance(data, dict):
        return None, 'the policy must be a JSON object'
    extra = sorted(set(data) - set(KEYS))
    if extra:
        return None, 'unknown policy key(s): ' + ', '.join(map(str, extra))
    base = data.get('base')
    if not isinstance(base, str) or not base.strip():
        return None, '`base` must be a non-empty string'
    paths = data.get('restricted_paths')
    if not isinstance(paths, list) or not all(isinstance(p, str) and p.strip() for p in paths):
        return None, '`restricted_paths` must be a list of non-empty strings'
    out = {'base': base.strip(), 'restricted_paths': [p.strip() for p in paths]}
    if 'merge' in data:
        merge = data['merge']
        if not isinstance(merge, dict):
            return None, '`merge` must be an object'
        extra = sorted(set(merge) - set(MERGE_KEYS))
        if extra:
            return None, 'unknown merge key(s): ' + ', '.join(map(str, extra))
        if 'review' in merge and merge['review'] not in MERGE_REVIEW:
            return None, '`merge.review` must be one of: ' + ', '.join(MERGE_REVIEW)
        if 'method' in merge and merge['method'] not in MERGE_METHODS:
            return None, '`merge.method` must be one of: ' + ', '.join(MERGE_METHODS)
        if 'allow_self_merge' in merge and not isinstance(merge['allow_self_merge'], bool):
            return None, '`merge.allow_self_merge` must be true or false'
        out['merge'] = dict(merge)
    return out, None


AUTONOMY_PATH = '.claude/auto.json'
AUTONOMY = ('auto', 'manual')
DEFAULT_RESTRICTED = ['.env', '.env.*', '*/.env', '*/.env.*', '*.pem', '*.key']


def load_autonomy(read, base_ref):
    """The project's `autonomy` (`.claude/auto.json` on the base) -> "auto" (the default: no file or no key) or "manual".
    A file that cannot be read or parsed, or a value other than the two, counts as "manual": unclear means a person."""
    try:
        got = read(base_ref, AUTONOMY_PATH)
    except Exception:  # an injected reader may fail any way it likes
        return 'manual'
    if not got:
        return 'auto'
    try:
        data = json.loads(got[0])
    except (TypeError, ValueError):
        return 'manual'
    value = data.get('autonomy', 'auto') if isinstance(data, dict) else None
    return value if value in AUTONOMY else 'manual'


def default_policy(base_ref):
    """The policy an auto-mode project gets when it has no policy file: its base branch, only secrets restricted."""
    base = (base_ref or 'main').split('/', 1)[1] if (base_ref or '').startswith('origin/') else (base_ref or 'main')
    return {'base': base, 'restricted_paths': list(DEFAULT_RESTRICTED)}


def gate_inputs(read, base_ref):
    """-> {policy, sha, error, autonomy} for one pass. In auto mode a missing policy file is not a stop: the default
    policy applies (an invalid file still is: a person fixes it)."""
    loaded = load_policy(read, base_ref)
    autonomy = load_autonomy(read, base_ref)
    if autonomy == 'auto' and loaded['policy'] is None and (loaded['error'] or '').endswith(f'missing on {base_ref}'):
        loaded = {'policy': default_policy(base_ref), 'sha': None, 'error': None}
    return {**loaded, 'autonomy': autonomy}


def load_policy(read, base_ref):
    """Read the policy from `base_ref` through `read(ref, path)`. -> dict with policy (dict or None), sha and error.
    A missing file, an unreadable one or an invalid one all give policy None: the plan is left for a person."""
    try:
        got = read(base_ref, POLICY_PATH)
    except Exception as e:  # an injected reader may fail any way it likes
        return {'policy': None, 'sha': None, 'error': f'the policy file could not be read: {e}'}
    if not got:
        return {'policy': None, 'sha': None, 'error': f'{POLICY_PATH} is missing on {base_ref}'}
    text, sha = got
    policy, error = parse_policy(text)
    return {'policy': policy, 'sha': sha, 'error': error}


def _norm(path):
    path = path.strip()
    return path[2:] if path.startswith('./') else path


def is_restricted(path, patterns):
    path = _norm(path)
    for pat in patterns:
        pat = _norm(pat)
        if pat.endswith('/**'):
            pat = pat[:-2]
        if pat.endswith('/'):
            if path.startswith(pat) or path + '/' == pat:
                return True
        elif fnmatchcase(path, pat):
            return True
    return False


def named_paths(text):
    out = set()
    for tok in PATH_TOKEN.findall(text or ''):
        tok = tok.rstrip('.,;:')
        if tok and ('/' in tok or '.' in tok):
            out.add(tok)
    return out


def _needs_clear(plan):
    lines = (plan or '').splitlines()
    for n, line in enumerate(lines):
        m = NEEDS.match(line.strip())
        if m:
            body = []
            for rest in lines[n + 1:]:
                h = re.match(r'^(#{1,6})\s', rest)
                if h and len(h.group(1)) <= len(m.group(1)):
                    break
                body.append(rest)
            return '\n'.join(body).strip().lower() in ('none.', '**none.**')
    return False


def needs_lines(plan):
    """The non-empty lines of the plan's "Needs a person" section (none when it says `None.` or has no such section)."""
    lines = (plan or '').splitlines()
    for n, line in enumerate(lines):
        m = NEEDS.match(line.strip())
        if m:
            body = []
            for rest in lines[n + 1:]:
                h = re.match(r'^(#{1,6})\s', rest)
                if h and len(h.group(1)) <= len(m.group(1)):
                    break
                if rest.strip() and rest.strip().lower() not in ('none.', '**none.**'):
                    body.append(rest.strip().lstrip('-* ').strip())
            return body
    return []


def plan_gate(task, policy, autonomy='manual'):
    if not policy:
        return {'verdict': 'person', 'reasons': ['no valid supervisor policy: the plan is left for a person']}
    reasons, person, changes = [], False, False
    if task.get('status') != 'plan_review':
        person = True
        reasons.append(f'the task is not awaiting plan review (status {task.get("status")!r})')
    plan, items = task.get('plan') or '', task.get('items') or ''
    parsed = queue.parse_items(items)
    if not parsed:
        changes = True
        reasons.append('the plan has no queue items')
    if not _needs_clear(plan):
        if autonomy == 'auto':          # nobody waits: the supervisor takes the open points as decided and says so
            open_points = needs_lines(plan)
            reasons.append('decided by the supervisor in auto mode: ' + ('; '.join(open_points) if open_points
                                                                        else 'no "Needs a person" section'))
        else:
            person = True
            reasons.append('"Needs a person" is not "None."')
    for item_id, _checked, block in parsed:
        if not ACCEPTANCE.search(block):
            changes = True
            reasons.append(f'item {item_id} has no Acceptance line')
    hits = sorted(p for p in named_paths(plan + '\n' + items) if is_restricted(p, policy['restricted_paths']))
    if hits:
        changes = True
        reasons.append('the plan names restricted path(s): ' + ', '.join(hits))
    return {'verdict': 'person' if person else 'changes' if changes else 'approve', 'reasons': reasons}


def merge_gate(task, pr, checks, reviews, policy, merger, files=None, autonomy='manual'):
    """The deterministic merge gate (decision 0010, 4 and 6). Pure: state in, verdict out; PR prose is never read.

    `pr` is the forge's pull request dict, `checks` and `reviews` the forge lists, `files` the changed paths (or
    `pr['files']`), `merger` the identity that would merge. Six conditions, all evaluated so every failure is listed:
      1. open       the PR is open, not a draft, not already merged, and the task has no `merged_at`
      2. base       the PR targets the policy `base`
      3. ci         at least one check, and every check is `success`
      4. review     an APPROVED review by someone other than the author, on the current head SHA, with no later
                    CHANGES_REQUESTED from the same reviewer (skipped when policy `merge.review` is "none")
      5. paths      no changed file is restricted
      6. identity   the PR author is not the merger
    -> {"verdict": "merge" | "refuse", "reasons": [...], "evidence": {...}, "method": ..., "sha": head sha}.
    No policy, or an unknown head SHA, always refuses."""
    pr, task = pr or {}, task or {}
    if not policy:
        return {'verdict': 'refuse', 'reasons': ['no valid supervisor policy: nothing is merged'],
                'evidence': {}, 'method': None, 'sha': pr.get('head_sha')}
    merge = dict(MERGE_DEFAULTS, **(policy.get('merge') or {}))
    sha = pr.get('head_sha') or ''
    files = list(files if files is not None else pr.get('files') or [])
    author = pr.get('author') or ''
    reasons = []
    if not sha:
        reasons.append('the pull request has no head SHA to merge')
    if pr.get('state') != 'open' or pr.get('draft') or pr.get('merged') or task.get('merged_at'):
        reasons.append('the pull request is not open, is a draft, or is already merged')
    if pr.get('base') != policy['base']:
        reasons.append(f'the pull request targets {pr.get("base")!r}, not {policy["base"]!r}')
    bad = sorted(c.get('name', '') for c in checks or [] if c.get('state') != 'success')
    if not checks:
        reasons.append('no CI checks have run')
    elif bad:
        reasons.append('checks not green: ' + ', '.join(bad))
    approvals = []
    if merge['review'] == 'required':
        latest = {}
        for r in reviews or []:
            user = r.get('user') or ''
            if r.get('state') in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED') and user != author:
                latest[user] = r
        approvals = sorted(u for u, r in latest.items()
                           if r['state'] == 'APPROVED' and r.get('commit_id') == sha)
        if not approvals:
            reasons.append('no current approving review from someone other than the author')
    hits = sorted(f for f in files if is_restricted(f, policy['restricted_paths']))
    if hits:
        reasons.append('the pull request touches restricted path(s): ' + ', '.join(hits))
    self_ok = merge.get('allow_self_merge', autonomy == 'auto')     # auto mode: the runner's own PRs may be merged
    if not author or not merger or (author == merger and not self_ok):
        reasons.append('the merger must be a different identity from the pull request author')
    evidence = {'head_sha': sha, 'base': pr.get('base'), 'author': author, 'merger': merger,
                'checks': {c.get('name', ''): c.get('state') for c in checks or []},
                'approvals': approvals, 'review': merge['review'], 'files': len(files), 'restricted': hits,
                'self_merge': bool(author) and author == merger and self_ok}
    return {'verdict': 'refuse' if reasons else 'merge', 'reasons': reasons, 'evidence': evidence,
            'method': merge['method'], 'sha': sha}


ORDER_CLOSED = ('done', 'rejected')


def order(tasks):
    """The queue order (decision 0010, step 3). Pure: tasks in, ranks out; it never reads prose.

    `tasks` are the state's task dicts (`number`, `status`, `priority`, `depends_on`, `created_at`). Tasks that are done
    or rejected are left out. The rest are sorted by, in this order:
      1. priority, lower first, none last  -- a person's priority is never crossed
      2. unblock count, higher first      -- how many other open tasks list this one in `depends_on`
      3. age, oldest `created_at` first
      4. number
    -> [{"number", "rank" (1, 2, 3, ...), "reason"}] best first."""
    open_tasks = [t for t in tasks or [] if t.get('status') not in ORDER_CLOSED]
    unblocks = {t['number']: 0 for t in open_tasks}
    for t in open_tasks:
        for dep in set(t.get('depends_on') or []):
            if dep in unblocks and dep != t['number']:
                unblocks[dep] += 1
    ranked = sorted(open_tasks, key=lambda t: (t.get('priority') is None, t.get('priority') or 0, -unblocks[t['number']],
                                               t.get('created_at') or '', t['number']))
    out = []
    for n, t in enumerate(ranked, 1):
        count = unblocks[t['number']]
        parts = ['priority %s' % t['priority'] if t.get('priority') is not None else 'no priority']
        if count:
            parts.append('unblocks %d task%s' % (count, '' if count == 1 else 's'))
        parts.append('created %s' % (t.get('created_at') or 'unknown'))
        out.append({'number': t['number'], 'rank': n, 'reason': ', '.join(parts)})
    return out
