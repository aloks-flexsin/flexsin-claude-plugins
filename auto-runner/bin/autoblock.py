"""
Blockers and decisions as durable local records (roadmap K2, sections 5.2, 5.4 and 5.6; raising them is K3).

    python autoblock.py list [--auto DIR] [--state S]     # one line per blocker: id, state, task, question, what the state means
    python autoblock.py answer ID --option LABEL [--text TEXT] [--context C] [--responder NAME] [--role ROLE]
    python autoblock.py view [--auto DIR]                 # rewrite DIR/QUESTIONS.md and print where it is

Schema `blocker.v1` (every field present, nothing else):
    schema, id, project, task, run, agent_role, stage, blocking_artifact ({path, hash} or null), class (B1-B5),
    question, why_needed, prevents, impact_if_delayed, impact_if_wrong, evidence[], options[] ({label, tradeoffs}),
    recommendation, assumptions[], downstream[], parent_blocker (id or null), state, history[], created_at,
    owner_role, due (ISO 8601 or null), idempotency_key
Schema `decision.v1`: schema, blocker_id, version (1, 2, ...), responder, role, answer ({option} and/or {text}),
    context, created_at, applies_to (the artifact version the answer applies to, or null)

Lifecycle: OPEN -> ANSWERED -> PENDING_DELIVERY -> DELIVERED -> ACKNOWLEDGED -> RESUMING -> RESUMED -> CLOSED, plus the
side states NEEDS_CLARIFICATION, DELIVERY_FAILED, SUPERSEDED, WITHDRAWN, ESCALATED. Every legal move is in TRANSITIONS
with the component that owns it; anything else raises IllegalTransition naming both states. CLOSED, SUPERSEDED and
WITHDRAWN are final. Only RESUMED and CLOSED count as unblocked: a saved answer is not a resumed item.

Store: `.auto/blockers/<id>.json`, written to a temp file and renamed (atomic). `history` only grows; a transition
carries an idempotency key and re-applying the same move with the same key changes nothing. A file that cannot be read
or is not a valid blocker raises CorruptBlocker and is never overwritten. Stdlib only.

Raising (the agent's file contract): instead of guessing, the agent writes one JSON object per question to `.auto/blockers/new/<name>.json`
(in the worktree it works in) with the blocker.v1 content fields: question, why_needed, prevents, impact_if_delayed, impact_if_wrong,
owner_role, class (B1-B5), options [{label, tradeoffs}], recommendation, and optionally evidence, assumptions, downstream, due,
blocking_artifact, parent_blocker, agent_role (default implementer), stage (default the item). The runner owns schema, id, project,
task, run, state, history, created_at and idempotency_key: whatever the file says for those is replaced. `ingest()` validates each file;
a valid one is stored OPEN under an id derived from its content and its task (so the same file twice is one blocker) and the file is
removed; an invalid one is moved to `.auto/blockers/rejected/` with a `.reasons.txt` beside it, never dropped. A file found before any
iteration started is rejected too (it cannot be tied to an item). `QUESTIONS.md` in the `.auto` folder is a generated, read-only view of
the blockers still waiting; it is rewritten from the store and never edited by hand.

Answering (K4): `answer ID --option LABEL and/or --text TEXT` saves the next `decision.v1` version to `.auto/decisions/<id>.v<N>.json`
(atomic, never overwritten) and moves the blocker ANSWERED then PENDING_DELIVERY; only an OPEN or ESCALATED blocker can be answered.
The answer is data for the next iteration, never a command. Delivery to the runner is autodeliver.py; `describe()` says what a state
means for a person, so a saved answer reads "waiting for delivery", never "resumed".
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

BLOCKER_SCHEMA = 'blocker.v1'
DECISION_SCHEMA = 'decision.v1'
CLASSES = ('B1', 'B2', 'B3', 'B4', 'B5')
MAIN_STATES = ('OPEN', 'ANSWERED', 'PENDING_DELIVERY', 'DELIVERED', 'ACKNOWLEDGED', 'RESUMING', 'RESUMED', 'CLOSED')
SIDE_STATES = ('NEEDS_CLARIFICATION', 'DELIVERY_FAILED', 'SUPERSEDED', 'WITHDRAWN', 'ESCALATED')
STATES = MAIN_STATES + SIDE_STATES
FINAL = ('CLOSED', 'SUPERSEDED', 'WITHDRAWN')
UNBLOCKED = ('RESUMED', 'CLOSED')

# (from, to) -> owning component. Side-state moves not spelled out in the roadmap table follow its prose:
# clarification returns to OPEN, a failed delivery is retried, a later decision supersedes any undelivered-or-unresumed one.
TRANSITIONS = {
    ('OPEN', 'ANSWERED'): 'central service',
    ('ANSWERED', 'PENDING_DELIVERY'): 'central service',
    ('PENDING_DELIVERY', 'DELIVERED'): 'runner',
    ('DELIVERED', 'ACKNOWLEDGED'): 'runner + central',
    ('ACKNOWLEDGED', 'RESUMING'): 'runner (stage author)',
    ('RESUMING', 'RESUMED'): 'runner',
    ('RESUMED', 'CLOSED'): 'runner',
    ('OPEN', 'NEEDS_CLARIFICATION'): 'central service',
    ('RESUMING', 'NEEDS_CLARIFICATION'): 'stage author',
    ('NEEDS_CLARIFICATION', 'OPEN'): 'runner',
    ('PENDING_DELIVERY', 'DELIVERY_FAILED'): 'runner',
    ('DELIVERY_FAILED', 'PENDING_DELIVERY'): 'central service',
    ('DELIVERY_FAILED', 'DELIVERED'): 'runner',
    ('OPEN', 'ESCALATED'): 'central service',
    ('ESCALATED', 'ANSWERED'): 'central service',
    ('OPEN', 'WITHDRAWN'): 'runner',
    ('NEEDS_CLARIFICATION', 'WITHDRAWN'): 'runner',
    ('ESCALATED', 'WITHDRAWN'): 'runner',
    ('ANSWERED', 'SUPERSEDED'): 'central service',
    ('PENDING_DELIVERY', 'SUPERSEDED'): 'central service',
    ('DELIVERY_FAILED', 'SUPERSEDED'): 'central service',
    ('DELIVERED', 'SUPERSEDED'): 'central service',
    ('ACKNOWLEDGED', 'SUPERSEDED'): 'central service',
    ('NEEDS_CLARIFICATION', 'SUPERSEDED'): 'central service',
}

BLOCKER_FIELDS = ('schema', 'id', 'project', 'task', 'run', 'agent_role', 'stage', 'blocking_artifact', 'class',
                  'question', 'why_needed', 'prevents', 'impact_if_delayed', 'impact_if_wrong', 'evidence', 'options',
                  'recommendation', 'assumptions', 'downstream', 'parent_blocker', 'state', 'history', 'created_at',
                  'owner_role', 'due', 'idempotency_key')
DECISION_FIELDS = ('schema', 'blocker_id', 'version', 'responder', 'role', 'answer', 'context', 'created_at', 'applies_to')
HISTORY_FIELDS = ('from', 'to', 'by', 'time', 'key', 'reason')
_TEXT = ('id', 'project', 'task', 'run', 'agent_role', 'stage', 'question', 'why_needed', 'prevents',
         'impact_if_delayed', 'impact_if_wrong', 'created_at', 'owner_role', 'idempotency_key')
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
# What a person needs to answer a blocker away from the terminal; sent with the `raised` event (see content()).
CONTENT_FIELDS = ('question', 'why_needed', 'prevents', 'impact_if_delayed', 'impact_if_wrong', 'class', 'owner_role',
                  'options', 'recommendation', 'assumptions', 'evidence', 'due')


class IllegalTransition(ValueError):
    def __init__(self, src, dst):
        super().__init__(f'illegal transition: {src} -> {dst}')
        self.src, self.dst = src, dst


class CorruptBlocker(Exception):
    """The stored file is unreadable or not a valid blocker.v1; it is left exactly as found."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _iso(value):
    try:
        datetime.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def is_unblocked(state):
    return state in UNBLOCKED


_DESCRIBE = {'OPEN': 'waiting for an answer', 'ANSWERED': 'answer saved, waiting for delivery',
             'PENDING_DELIVERY': 'answer saved, waiting for delivery', 'DELIVERED': 'answer delivered, waiting for the runner',
             'ACKNOWLEDGED': 'runner has the answer, not resumed yet', 'RESUMING': 'runner has the answer, not resumed yet',
             'RESUMED': 'resumed', 'CLOSED': 'closed', 'NEEDS_CLARIFICATION': 'needs clarification',
             'DELIVERY_FAILED': 'delivery failed, retrying', 'SUPERSEDED': 'superseded', 'WITHDRAWN': 'withdrawn',
             'ESCALATED': 'escalated, waiting for an answer'}


def describe(state):
    """What a state means to a person; only RESUMED and CLOSED say the work is moving again."""
    return _DESCRIBE[state]


def owner_of(src, dst):
    if (src, dst) not in TRANSITIONS:
        raise IllegalTransition(src, dst)
    return TRANSITIONS[(src, dst)]


def new_blocker(**fields):
    """A blocker.v1 in state OPEN with empty lists and nulls for whatever is not given; `id` is assigned if absent."""
    b = {f: '' for f in _TEXT}
    b.update({'schema': BLOCKER_SCHEMA, 'id': 'B-' + uuid.uuid4().hex[:10], 'blocking_artifact': None, 'class': 'B1',
              'evidence': [], 'options': [], 'recommendation': '', 'assumptions': [], 'downstream': [],
              'parent_blocker': None, 'state': 'OPEN', 'history': [], 'created_at': _now(), 'due': None})
    b['idempotency_key'] = b['id']
    b.update(fields)
    return b


def content(b):
    """-> the parts of a blocker a person needs to answer it (CONTENT_FIELDS); runner bookkeeping and history stay local."""
    return {f: b[f] for f in CONTENT_FIELDS if f in b}


def validate_blocker(b):
    """-> list of problems (empty = a valid blocker.v1)."""
    if not isinstance(b, dict):
        return ['blocker is not an object']
    errors = [f'missing field: {f}' for f in BLOCKER_FIELDS if f not in b]
    errors += [f'unknown field: {f}' for f in b if f not in BLOCKER_FIELDS]
    if errors:
        return errors
    if b['schema'] != BLOCKER_SCHEMA:
        errors.append(f'schema: expected {BLOCKER_SCHEMA}')
    for f in _TEXT:
        if not isinstance(b[f], str) or not b[f].strip():
            errors.append(f'{f}: must be a non-empty string')
    if isinstance(b['id'], str) and b['id'] and not _ID.match(b['id']):
        errors.append('id: letters, digits, "-", "_" and "." only, at most 64')
    if b['class'] not in CLASSES:
        errors.append(f'class: must be one of {", ".join(CLASSES)}')
    if b['state'] not in STATES:
        errors.append(f'state: must be one of {", ".join(STATES)}')
    art = b['blocking_artifact']
    if art is not None and not (isinstance(art, dict) and set(art) == {'path', 'hash'}
                                and all(isinstance(v, str) and v for v in art.values())):
        errors.append('blocking_artifact: must be null or {path, hash}')
    for f in ('evidence', 'assumptions', 'downstream', 'history'):
        if not isinstance(b[f], list):
            errors.append(f'{f}: must be a list')
    if not isinstance(b['recommendation'], str):
        errors.append('recommendation: must be a string')
    if not isinstance(b['options'], list) or not all(
            isinstance(o, dict) and isinstance(o.get('label'), str) and o['label'] and isinstance(o.get('tradeoffs'), str)
            for o in b['options']):
        errors.append('options: must be a list of {label, tradeoffs}')
    if b['parent_blocker'] is not None and not isinstance(b['parent_blocker'], str):
        errors.append('parent_blocker: must be null or a blocker id')
    if isinstance(b['created_at'], str) and b['created_at'] and not _iso(b['created_at']):
        errors.append('created_at: not ISO 8601')
    if b['due'] is not None and not _iso(b['due']):
        errors.append('due: must be null or ISO 8601')
    if isinstance(b['history'], list):
        for i, h in enumerate(b['history']):
            if not isinstance(h, dict) or set(h) != set(HISTORY_FIELDS):
                errors.append(f'history[{i}]: must have exactly {", ".join(HISTORY_FIELDS)}')
    return errors


def validate_decision(d):
    """-> list of problems (empty = a valid decision.v1)."""
    if not isinstance(d, dict):
        return ['decision is not an object']
    errors = [f'missing field: {f}' for f in DECISION_FIELDS if f not in d]
    errors += [f'unknown field: {f}' for f in d if f not in DECISION_FIELDS]
    if errors:
        return errors
    if d['schema'] != DECISION_SCHEMA:
        errors.append(f'schema: expected {DECISION_SCHEMA}')
    for f in ('blocker_id', 'responder', 'role', 'created_at'):
        if not isinstance(d[f], str) or not d[f].strip():
            errors.append(f'{f}: must be a non-empty string')
    if not isinstance(d['version'], int) or isinstance(d['version'], bool) or d['version'] < 1:
        errors.append('version: must be an integer from 1')
    ans = d['answer']
    if not (isinstance(ans, dict) and ans and set(ans) <= {'option', 'text'}
            and all(isinstance(v, str) and v.strip() for v in ans.values())):
        errors.append('answer: must hold a non-empty option and/or text')
    if not isinstance(d['context'], str):
        errors.append('context: must be a string')
    if isinstance(d['created_at'], str) and d['created_at'] and not _iso(d['created_at']):
        errors.append('created_at: not ISO 8601')
    if d['applies_to'] is not None and not isinstance(d['applies_to'], str):
        errors.append('applies_to: must be null or a version string')
    return errors


def new_decision(blocker_id, version, responder, role, option=None, text=None, context='', applies_to=None):
    answer = {k: v for k, v in (('option', option), ('text', text)) if v}
    d = {'schema': DECISION_SCHEMA, 'blocker_id': blocker_id, 'version': version, 'responder': responder, 'role': role,
         'answer': answer, 'context': context, 'created_at': _now(), 'applies_to': applies_to}
    errors = validate_decision(d)
    if errors:
        raise ValueError('; '.join(errors))
    return d


def write_json_atomic(path, obj):
    """Write to a temp file in the same folder, then rename over `path`: a reader sees the old file or the new one, never half."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, indent=2, sort_keys=True)
            f.write('\n')
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Store:
    """Blockers under `<auto_dir>/blockers/<id>.json`, their decisions under `<auto_dir>/decisions/<id>.v<N>.json`."""

    def __init__(self, auto_dir):
        self.dir = Path(auto_dir) / 'blockers'
        self.decisions_dir = Path(auto_dir) / 'decisions'

    def path(self, blocker_id):
        if not isinstance(blocker_id, str) or not _ID.match(blocker_id):
            raise ValueError(f'bad blocker id: {blocker_id!r}')
        return self.dir / f'{blocker_id}.json'

    def _write(self, b):
        write_json_atomic(self.path(b['id']), b)

    def decisions(self, blocker_id):
        """-> every stored decision for the blocker, oldest version first. An invalid file raises CorruptBlocker."""
        self.path(blocker_id)
        out = []
        mine = re.compile(re.escape(blocker_id) + r'\.v[0-9]+\.json')
        for p in self.decisions_dir.glob('*.json') if self.decisions_dir.exists() else []:
            if not mine.fullmatch(p.name):
                continue
            try:
                d = json.loads(p.read_text(encoding='utf-8'))
            except (OSError, ValueError) as e:
                raise CorruptBlocker(f'{p.name}: unreadable ({type(e).__name__})') from None
            errors = validate_decision(d)
            if errors or d['blocker_id'] != blocker_id or p.name != f'{blocker_id}.v{d["version"]}.json':
                raise CorruptBlocker(f'{p.name}: ' + ('; '.join(errors) or 'does not match its file name'))
            out.append(d)
        return sorted(out, key=lambda d: d['version'])

    def latest_decision(self, blocker_id):
        found = self.decisions(blocker_id)
        return found[-1] if found else None

    def answer(self, blocker_id, responder, role, option=None, text=None, context='', applies_to=None):
        """Save the next decision version, then OPEN/ESCALATED -> ANSWERED -> PENDING_DELIVERY. -> (blocker, decision)."""
        b = self.load(blocker_id)
        if b['state'] not in ('OPEN', 'ESCALATED'):
            raise IllegalTransition(b['state'], 'ANSWERED')
        if option and b['options'] and option not in [o['label'] for o in b['options']]:
            raise ValueError(f'option: {option!r} is not one of the blocker\'s options')
        version = len(self.decisions(blocker_id)) + 1
        d = new_decision(blocker_id, version, responder, role, option, text, context, applies_to)
        path = self.decisions_dir / f'{blocker_id}.v{version}.json'
        if path.exists():
            raise CorruptBlocker(f'{path.name}: already exists and is never overwritten')
        write_json_atomic(path, d)
        self.transition(blocker_id, 'ANSWERED', f'answer-v{version}', reason=f'decision v{version} by {role}')
        b = self.transition(blocker_id, 'PENDING_DELIVERY', f'pending-v{version}', reason=f'decision v{version} stored')
        return b, d

    def receive(self, decision):
        """Store a decision made elsewhere (the central service) exactly as given, then OPEN/ESCALATED -> ANSWERED -> PENDING_DELIVERY.
        The same decision again is a no-op (same transition keys as answer()); a different one under a stored version is refused."""
        errors = validate_decision(decision)
        if errors:
            raise ValueError('; '.join(errors))
        bid, v = decision['blocker_id'], decision['version']
        b = self.load(bid)
        path = self.decisions_dir / f'{bid}.v{v}.json'
        if path.exists():
            if json.loads(path.read_text(encoding='utf-8')) != decision:
                raise CorruptBlocker(f'{path.name}: a different decision is already stored and is never overwritten')
        elif b['state'] in ('OPEN', 'ESCALATED'):
            expected = len(self.decisions(bid)) + 1
            if v != expected:
                raise ValueError(f'version: expected {expected}, got {v}')
            write_json_atomic(path, decision)
        else:
            raise IllegalTransition(b['state'], 'ANSWERED')
        if b['state'] in ('OPEN', 'ESCALATED'):          # also finishes a receive that stopped after writing the file
            self.transition(bid, 'ANSWERED', f'answer-v{v}', reason=f'decision v{v} by {decision["role"]} (received)')
            b = self.transition(bid, 'PENDING_DELIVERY', f'pending-v{v}', reason=f'decision v{v} stored')
        return b

    def load(self, blocker_id):
        p = self.path(blocker_id)
        try:
            b = json.loads(p.read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as e:
            raise CorruptBlocker(f'{p.name}: unreadable ({type(e).__name__})') from None
        errors = validate_blocker(b)
        if errors:
            raise CorruptBlocker(f'{p.name}: ' + '; '.join(errors))
        if b['id'] != blocker_id:
            raise CorruptBlocker(f'{p.name}: id inside is {b["id"]!r}')
        return b

    def create(self, blocker, by='runner'):
        """Store a new blocker as OPEN. The same id with the same idempotency key again is a no-op; a different key is refused."""
        b = dict(blocker)
        b['state'], b['history'] = 'OPEN', [{'from': None, 'to': 'OPEN', 'by': by, 'time': _now(),
                                             'key': b.get('idempotency_key'), 'reason': 'created'}]
        errors = validate_blocker(b)
        if errors:
            raise ValueError('; '.join(errors))
        if self.path(b['id']).exists():
            existing = self.load(b['id'])
            if existing['idempotency_key'] == b['idempotency_key']:
                return existing
            raise ValueError(f'blocker {b["id"]} already exists with another idempotency key')
        self._write(b)
        return b

    def transition(self, blocker_id, to, key, by=None, reason=''):
        """Move to `to`. `key` identifies this move: the same key and target again returns the record unchanged."""
        if not isinstance(key, str) or not key:
            raise ValueError('key: a non-empty idempotency key is required')
        b = self.load(blocker_id)
        for h in b['history']:
            if h['key'] == key and h['from'] is not None:
                if h['to'] == to:
                    return b
                raise ValueError(f'key {key!r} was already used for {h["from"]} -> {h["to"]}')
        owner = owner_of(b['state'], to)
        b['history'] = b['history'] + [{'from': b['state'], 'to': to, 'by': by or owner, 'time': _now(),
                                        'key': key, 'reason': reason}]
        b['state'] = to
        self._write(b)
        return b

    def all(self):
        """-> (blockers, problems); a corrupt file is listed as a problem, not skipped silently and not touched."""
        out, problems = [], []
        for p in sorted(self.dir.glob('*.json')) if self.dir.exists() else []:
            if p.name.startswith('.tmp-'):
                continue
            try:
                out.append(self.load(p.stem))
            except (CorruptBlocker, ValueError) as e:
                problems.append(str(e))
        return out, problems


NEW, REJECTED, VIEW = 'new', 'rejected', 'QUESTIONS.md'
RUNNER_FIELDS = ('schema', 'id', 'project', 'task', 'run', 'state', 'history', 'created_at', 'idempotency_key')


def from_file(raw, task, run, project):
    """-> (blocker or None, problems) for the text of one file the agent wrote."""
    try:
        data = json.loads(raw)
    except ValueError as e:
        return None, [f'not valid JSON ({getattr(e, "msg", e)})']
    if not isinstance(data, dict):
        return None, ['must be one JSON object']
    if data.get('schema', BLOCKER_SCHEMA) != BLOCKER_SCHEMA:
        return None, [f'schema: expected {BLOCKER_SCHEMA}']
    fields = {k: v for k, v in data.items() if k not in RUNNER_FIELDS}
    fields.setdefault('agent_role', 'implementer')
    fields.setdefault('stage', task)
    key = hashlib.sha256(f'{task}\n{raw}'.encode('utf-8')).hexdigest()
    b = new_blocker(**fields)
    b.update(id='B-' + key[:12], project=project, task=task, run=run, idempotency_key=key)
    errors = validate_blocker(b)
    return (None if errors else b), errors


def _reject(path, errors, rejected_dir, stamp):
    rejected_dir.mkdir(parents=True, exist_ok=True)
    dest, n = rejected_dir / f'{path.stem}-{stamp}.json', 1
    while dest.exists():
        n += 1
        dest = rejected_dir / f'{path.stem}-{stamp}-{n}.json'
    shutil.move(str(path), str(dest))
    dest.with_suffix('.reasons.txt').write_text('\n'.join(errors) + '\n', encoding='utf-8')
    return dest


def ingest(new_dirs, auto_dir, task, run, project, stamp=None):
    """Take every `*.json` in `new_dirs` -> list of {file, id, question, errors, moved_to} in the order found.
    `task` None means no iteration is running: every file is rejected. Folders are only created when something is stored or rejected."""
    store = Store(auto_dir)
    stamp = stamp or datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    out = []
    for d in new_dirs:
        d = Path(d)
        for p in sorted(d.glob('*.json')) if d.is_dir() else []:
            try:
                raw = p.read_bytes().decode('utf-8')
            except (OSError, UnicodeDecodeError):
                b, errors = None, ['not readable as UTF-8 text']
            else:
                if task:
                    b, errors = from_file(raw, task, run, project)
                else:
                    b, errors = None, ['found before any iteration started, so it cannot be tied to an item; raise it again from inside one']
            if b is not None:
                try:
                    b = store.create(b)
                except (ValueError, CorruptBlocker) as e:
                    b, errors = None, [str(e)]
            if b is None:
                dest = _reject(p, errors, store.dir / REJECTED, stamp)
                out.append({'file': p.name, 'id': None, 'question': '', 'errors': errors, 'moved_to': str(dest)})
            else:
                p.unlink()
                out.append({'file': p.name, 'id': b['id'], 'question': b['question'], 'errors': [], 'moved_to': None})
    return out


def waiting(blockers):
    """Blockers a person still has to act on (not resumed and not final), oldest first."""
    return sorted((b for b in blockers if b['state'] not in UNBLOCKED + FINAL), key=lambda b: (b['created_at'], b['id']))


def render_view(blockers, problems=()):
    lines = ['# Questions from unattended runs (generated)', '',
             'Generated by autoblock.py from `.auto/blockers/`. Do not edit this file: it is rewritten from the blocker store.', '']
    open_ = waiting(blockers)
    if not open_:
        lines += ['(no blocker is waiting)', '']
    for b in open_:
        lines += [f'## {b["id"]} ({b["task"]}, {b["state"]}, class {b["class"]})', '', b['question'], '',
                  f'- Why needed: {b["why_needed"]}', f'- Prevents: {b["prevents"]}',
                  f'- If delayed: {b["impact_if_delayed"]}', f'- If wrong: {b["impact_if_wrong"]}',
                  f'- Who answers: {b["owner_role"]}' + (f' (due {b["due"]})' if b['due'] else '')]
        lines += [f'- Option "{o["label"]}": {o["tradeoffs"]}' for o in b['options']]
        if b['recommendation']:
            lines.append(f'- Recommendation: {b["recommendation"]}')
        lines.append('')
    if problems:
        lines += ['## Unreadable blocker files', ''] + [f'- {p}' for p in problems] + ['']
    return '\n'.join(lines)


def write_view(auto_dir):
    """Rewrite `<auto_dir>/QUESTIONS.md` atomically from the store -> its path."""
    auto_dir = Path(auto_dir)
    auto_dir.mkdir(parents=True, exist_ok=True)
    text = render_view(*Store(auto_dir).all())
    fd, tmp = tempfile.mkstemp(dir=auto_dir, prefix='.tmp-', suffix='.md')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, auto_dir / VIEW)
    return auto_dir / VIEW


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=['list', 'view', 'answer'])
    ap.add_argument('id', nargs='?', help='blocker id (answer)')
    ap.add_argument('--auto', default=str(Path(os.environ.get('CLAUDE_PROJECT_DIR') or '.') / '.auto'), metavar='DIR')
    ap.add_argument('--state', choices=STATES)
    ap.add_argument('--option', help='answer: the label of the chosen option')
    ap.add_argument('--text', help='answer: free text')
    ap.add_argument('--context', default='', help='answer: business rules or background')
    ap.add_argument('--responder', default=os.environ.get('USERNAME') or os.environ.get('USER') or 'person')
    ap.add_argument('--role', default='owner')
    a = ap.parse_args(argv)
    if a.command == 'answer':
        if not a.id or not (a.option or a.text):
            ap.error('answer needs a blocker id and --option and/or --text')
        try:
            b, d = Store(a.auto).answer(a.id, a.responder, a.role, a.option, a.text, a.context)
        except FileNotFoundError:
            print(f'no blocker {a.id}', file=sys.stderr)
            return 1
        except (ValueError, CorruptBlocker) as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f'{b["id"]}: decision v{d["version"]} saved; {describe(b["state"])}')
        return 0
    if a.command == 'view':
        print(write_view(a.auto))
        return 0
    blockers, problems = Store(a.auto).all()
    for b in blockers:
        if a.state is None or b['state'] == a.state:
            print(f'{b["id"]}\t{b["state"]}\t{b["task"]}\t{b["question"]}\t({describe(b["state"])})')
    for p in problems:
        print(f'unreadable: {p}', file=sys.stderr)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
