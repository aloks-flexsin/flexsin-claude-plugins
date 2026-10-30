"""
Read and extend the autonomous queue file safely (used by the runner and the /auto command).

    python queue.py open  --queue FILE                       # how many items are still open
    python queue.py check --queue FILE --items FILE          # validate new items, change nothing
    python queue.py add   --queue FILE --items FILE [--first]  # validate, then insert (default: after the last open item)
    python queue.py status --queue FILE                      # one line per item: id, state, detail

An item is `- [ ] **ID Title.** what to build` followed by indented lines. It must carry an `*Acceptance:*` line (what proves it is done) and
a new ID (a letter or two plus digits, not used before). Items are never added already ticked. New items go before the "Needs a person"
heading so that list stays last. Nothing else in the file is rewritten.

An item may also carry one status line, `  *Status:* STATE [detail]`, written only by the runner. STATE is one of
queued | running | done <commit> | blocked <question> | rejected <reasons>. No status line on an open item means queued; a ticked
item without one means done. This file is the only one a person has to read.

An item may carry one dependency line, `  *Depends on:* A1, A2` (`*Depends_on:*` also reads), naming items that must be ticked first.
No such line means ready. An open item is *ready* when its state is not `blocked` and every dependency is ticked; the runner works
only ready items, in file order (`ready_items`), and ends when none is left. A blocked item stays skipped until a person deletes or
rewrites its status line.

An item may carry one browser line, `  *Browser:* journeys: sign-in, search; roles: admin, viewer` (both parts optional, any
order, comma-separated names). It opts the item into the runner's browser stage (autobrowser.run_stage); no line, no stage.

An item may carry one kind line, `  *Kind:* feature` (feature | fix | breaking | internal, any case), the changelog heading its
change goes under. No line reads as `internal`; `validate(..., require_kind=True)` (used when the changelog is on) asks for one.

Note: this file is called queue.py because the plugin layout asks for it. Never put its folder on sys.path ahead of the standard
library (it would shadow `import queue`); load it by path, as scripts/auto_queue.py and the tests do.
"""
import argparse
import re
import sys
from pathlib import Path

ITEM = re.compile(r'^- \[( |x)\] \*\*([A-Z]{1,3}\d+)\b')
OPEN = re.compile(r'^- \[ \] ')
HEADING = re.compile(r'^#{1,6} ')
NEEDS_PERSON = re.compile(r'^## Needs a person', re.I)
STATUS = re.compile(r'^\s*\*Status:\*\s*(\S+)\s*(.*)$')
STATES = ('queued', 'running', 'done', 'blocked', 'rejected')
DEPENDS = re.compile(r'^\s*\*Depends[ _]on:\*\s*(.*)$', re.I)
ITEM_ID = re.compile(r'\b[A-Z]{1,3}\d+\b')
BROWSER = re.compile(r'^\s*\*Browser:\*\s*(.*)$', re.I)
BROWSER_PART = re.compile(r'^\s*(journeys|roles)\s*:\s*(.*)$', re.I)
KIND = re.compile(r'^\s*\*Kind:\*\s*(.*)$', re.I)
KINDS = ('feature', 'fix', 'breaking', 'internal')


def open_count(queue_text):
    return sum(1 for line in queue_text.splitlines() if OPEN.match(line))


def parse_items(text):
    """-> list of (id, checked, block_text). A block runs until the next item or heading."""
    items, cur = [], None
    for line in text.splitlines():
        m = ITEM.match(line)
        if m:
            cur = [m.group(2), m.group(1) == 'x', [line]]
            items.append(cur)
        elif cur is not None and not HEADING.match(line):
            cur[2].append(line)
        elif HEADING.match(line):
            cur = None
    return [(i, c, '\n'.join(b).rstrip()) for i, c, b in items]


def parse_status(block, checked=False):
    """-> (state, detail) from an item's block. Defaults: ticked = done, open = queued. An unknown word is reported as 'unknown'."""
    for line in block.splitlines():
        m = STATUS.match(line)
        if m:
            state, detail = m.group(1).lower().rstrip(':'), m.group(2).strip()
            return (state if state in STATES else 'unknown'), detail
    return ('done' if checked else 'queued'), ''


def statuses(queue_text):
    """-> list of (id, state, detail) in file order."""
    return [(i, *parse_status(b, c)) for i, c, b in parse_items(queue_text)]


def parse_depends(block):
    """-> list of item IDs from the item's `*Depends on:*` line, in the order written; [] when there is none."""
    for line in block.splitlines():
        m = DEPENDS.match(line)
        if m:
            return ITEM_ID.findall(m.group(1))
    return []


def parse_browser(block):
    """-> {'journeys': [...], 'roles': [...]} from the item's `*Browser:*` line, or None when it has none.
    A part that is not `journeys:` or `roles:` raises ValueError naming it, so a typo never silently drops the stage's roles."""
    for line in block.splitlines():
        m = BROWSER.match(line)
        if not m:
            continue
        spec = {'journeys': [], 'roles': []}
        for part in filter(str.strip, m.group(1).split(';')):
            pm = BROWSER_PART.match(part)
            if not pm:
                raise ValueError(f'*Browser:* part "{part.strip()}" must start with journeys: or roles:')
            spec[pm.group(1).lower()] += [n.strip() for n in pm.group(2).split(',') if n.strip()]
        return spec
    return None


def has_kind(block):
    return any(KIND.match(line) for line in block.splitlines())


def parse_kind(block):
    """-> one of KINDS from the item's `*Kind:*` line; 'internal' when it has none. An unknown word raises ValueError naming it."""
    for line in block.splitlines():
        m = KIND.match(line)
        if m:
            kind = m.group(1).strip().lower()
            if kind not in KINDS:
                raise ValueError(f'*Kind:* "{m.group(1).strip()}" must be one of {", ".join(KINDS)}')
            return kind
    return 'internal'


def readiness(queue_text, known=None):
    """-> list of (id, reason) for every open item, in file order; reason '' means ready.
    `known` (id -> (state, detail)) overrides the status lines in the text, because the runner keeps statuses outside the worktree copy."""
    items = parse_items(queue_text)
    ticked = {i for i, c, _ in items if c}
    ids = {i for i, _, _ in items}
    out = []
    for item_id, checked, block in items:
        if checked:
            continue
        state = (known or {}).get(item_id, parse_status(block))[0]
        if state == 'blocked':
            out.append((item_id, 'blocked'))
            continue
        unmet = [d for d in parse_depends(block) if d not in ticked]
        if unmet:
            out.append((item_id, 'waits for ' + ', '.join(d if d in ids else f'{d} (not in the queue)' for d in unmet)))
        else:
            out.append((item_id, ''))
    return out


def ready_items(queue_text, known=None):
    """-> IDs of the open items that may run now, in file order."""
    return [i for i, reason in readiness(queue_text, known) if not reason]


def set_status(queue_text, item_id, state, detail=''):
    """Write or replace the status line of one item; every other line is untouched. Unknown ids raise KeyError."""
    if state not in STATES:
        raise ValueError(f'unknown state {state!r}; use one of {", ".join(STATES)}')
    detail = ' '.join(detail.split())            # one line, so the parser can always read it back
    new = f'  *Status:* {state}' + (f' {detail}' if detail else '')
    lines, out, in_item, found, done = queue_text.splitlines(), [], False, False, False
    for line in lines:
        m = ITEM.match(line)
        if m:
            if in_item and not done:
                out.append(new)
                done = True
            in_item = m.group(2) == item_id
            found = found or in_item
            done = False
        elif in_item and HEADING.match(line):
            if not done:
                out.append(new)
            in_item, done = False, True
        elif in_item and STATUS.match(line):
            if not done:
                out.append(new)
                done = True
            continue
        out.append(line)
    if in_item and not done:
        out.append(new)
    if not found:
        raise KeyError(item_id)
    return '\n'.join(out).rstrip('\n') + '\n'


def validate(queue_text, items_text, require_kind=False):
    """-> (items, errors). Errors are plain sentences for the person who wrote the items.
    `require_kind` (the changelog is on) makes a missing `*Kind:*` line an error; off, old items without one stay valid."""
    existing = {i for i, _, _ in parse_items(queue_text)}
    items, errors = parse_items(items_text), []
    if not items:
        return items, ['No items found. Each one starts with: - [ ] **A1 Short title.** what to build']
    seen = set()
    for item_id, checked, block in items:
        if checked:
            errors.append(f'{item_id}: new items must start open ([ ]), not ticked')
        if item_id in existing or item_id in seen:
            errors.append(f'{item_id}: this ID is already used; pick the next free one')
        seen.add(item_id)
        if '*Acceptance:*' not in block and 'Acceptance:' not in block:
            errors.append(f'{item_id}: add an *Acceptance:* line that says what proves it is done (a test or a command)')
        try:
            parse_browser(block)
        except ValueError as e:
            errors.append(f'{item_id}: {e}')
        try:
            parse_kind(block)
        except ValueError as e:
            errors.append(f'{item_id}: {e}')
        if require_kind and not has_kind(block):
            errors.append(f'{item_id}: add a *Kind:* line ({", ".join(KINDS)}) so the changelog knows where the change goes')
        if len(block) < 80:
            errors.append(f'{item_id}: too short to work from; say what to build, where, and what must not change')
    return items, errors


def insert(queue_text, items_text, first=False):
    lines = queue_text.splitlines()
    block = items_text.strip('\n').splitlines() + ['']
    if first:
        at = next((i for i, line in enumerate(lines) if OPEN.match(line)), None)
    else:
        at = None
    if at is None:
        at = next((i for i, line in enumerate(lines) if NEEDS_PERSON.match(line)), len(lines))
    out = lines[:at] + block + lines[at:]
    return '\n'.join(out).rstrip('\n') + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=['open', 'check', 'add', 'status'])
    ap.add_argument('--queue', required=True)
    ap.add_argument('--items')
    ap.add_argument('--first', action='store_true', help='put the new items ahead of the open ones instead of after them')
    a = ap.parse_args(argv)
    queue = Path(a.queue)
    qtext = queue.read_text(encoding='utf-8')
    if a.command == 'open':
        print(open_count(qtext))
        return 0
    if a.command == 'status':
        for item_id, state, detail in statuses(qtext):
            print(f'{item_id}\t{state}' + (f'\t{detail}' if detail else ''))
        return 0
    if not a.items:
        ap.error('--items is required')
    itext = Path(a.items).read_text(encoding='utf-8')
    items, errors = validate(qtext, itext)
    if errors:
        print('Not added:\n- ' + '\n- '.join(errors), file=sys.stderr)
        return 1
    if a.command == 'add':
        queue.write_text(insert(qtext, itext, a.first), encoding='utf-8')
    print(('added ' if a.command == 'add' else 'ok: ') + ', '.join(i for i, _, _ in items))
    return 0


if __name__ == '__main__':
    sys.exit(main())
