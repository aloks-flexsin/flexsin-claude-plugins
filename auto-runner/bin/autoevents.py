"""
Local event ledger: one JSON line per thing that happened in a run, appended to `.auto/events.jsonl`. Never rewritten.

Schema `event.v1` (every field present, nothing else):
    schema          "event.v1"
    id              random hex, unique per event
    time            UTC, ISO 8601 to the second
    run             the run's stamp (same as the log names)
    project         the project folder name
    type            run_start | run_end | item_state | tests | blocker | browser | findings | changelog | docs
    item            queue item ID, or null for run_start / run_end
    detail          a small JSON object (item_state: {"from", "to"}; tests: {"result": "pass"|"fail", "exit"}; run_end: {"exit", "reason"};
                    blocker: {"action": "raised", "id", "blocker": autoblock.content()} or {"action": "rejected", "file", "reasons"} or
                    {"action": "delivered", "id", "delivery"} or {"action": "resumed", "id"};
                    browser: {"result": "pass"|"fail"|"invalid", "evidence": "E1"|"E3"|null, "failed", "counts", "errors"};
                    docs: {"result": "pass"|"fail", "map", "failures", "stale"})
    evidence_class  runner_observed (the runner saw it: git, queue, files) | independent_test (the runner's own test run) | agent_reported (only the agent says so)

A Ledger given `redact=` passes every detail through it first (the runner removes generated test credentials this way).
`runs.jsonl` stays the per-iteration summary; this file is the finer ledger. Stdlib only.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 'event.v1'
FILE = 'events.jsonl'
TYPES = ('run_start', 'run_end', 'item_state', 'tests', 'blocker', 'browser', 'findings', 'changelog', 'docs', 'publish')
RUN_TYPES = ('run_start', 'run_end', 'publish')    # about the whole run: no item
EVIDENCE = ('runner_observed', 'independent_test', 'agent_reported')
FIELDS = ('schema', 'id', 'time', 'run', 'project', 'type', 'item', 'detail', 'evidence_class')


def make(run, project, type, item=None, detail=None, evidence_class='runner_observed'):
    event = {'schema': SCHEMA, 'id': uuid.uuid4().hex, 'time': datetime.now(timezone.utc).isoformat(timespec='seconds'),
             'run': run, 'project': project, 'type': type, 'item': item, 'detail': dict(detail or {}),
             'evidence_class': evidence_class}
    errors = validate(event)
    if errors:
        raise ValueError('; '.join(errors))
    return event


def validate(event):
    """-> list of problems (empty = a valid event.v1)."""
    if not isinstance(event, dict):
        return ['event is not an object']
    errors = [f'missing field: {f}' for f in FIELDS if f not in event]
    errors += [f'unknown field: {f}' for f in event if f not in FIELDS]
    if errors:
        return errors
    if event['schema'] != SCHEMA:
        errors.append(f'schema: expected {SCHEMA}')
    for f in ('id', 'time', 'run', 'project'):
        if not isinstance(event[f], str) or not event[f]:
            errors.append(f'{f}: must be a non-empty string')
    if event['type'] not in TYPES:
        errors.append(f'type: must be one of {", ".join(TYPES)}')
    if event['evidence_class'] not in EVIDENCE:
        errors.append(f'evidence_class: must be one of {", ".join(EVIDENCE)}')
    if not isinstance(event['detail'], dict):
        errors.append('detail: must be an object')
    if event['type'] in RUN_TYPES:
        if event['item'] is not None:
            errors.append('item: must be null for run events')
    elif not isinstance(event['item'], str) or not event['item']:
        errors.append('item: required for item events')
    if isinstance(event['time'], str):
        try:
            datetime.fromisoformat(event['time'])
        except ValueError:
            errors.append('time: not ISO 8601')
    return errors


class Ledger:
    """Appends events for one run. Opening in append mode is the whole append-only guarantee: nothing here reads or rewrites old lines."""

    def __init__(self, auto_dir, run, project, redact=None):
        self.path = Path(auto_dir) / FILE
        self.run, self.project = run, project
        self.redact = redact            # detail -> detail with generated test credentials removed (autobrowser.redact_obj)

    def emit(self, type, item=None, detail=None, evidence_class='runner_observed'):
        if self.redact is not None:
            detail = self.redact(dict(detail or {}))
        event = make(self.run, self.project, type, item, detail, evidence_class)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(event) + '\n')
        return event


def read(auto_dir):
    path = Path(auto_dir) / FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
