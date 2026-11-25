"""The Supervisor's pass (decision 0010). Today: the plan step. Standard library only; all I/O is injected.

`plan_step(project, http, read, base_ref)` reads the project's state (`GET /api/v1/supervisor/state`), loads the policy from
the base ref through `read(ref, path) -> (text, blob_sha) | None` (autogates.load_policy), runs `plan_gate` on every task
that awaits plan review and has no supervisor decision on its current plan post (`plan_decided` is null), and posts the
verdict to `POST /api/v1/supervisor/tasks/<n>/plan-decision`: approve -> `approve`, changes -> `changes`, person ->
`escalate` (the plan stays for a person). The service sends a third `changes` back as `escalate` and ignores a repeated
decision for a plan it already decided, so running a pass twice never duplicates a decision.

`http(method, path, body=None) -> (status, json)` is the only way out: tests inject a fake, a real run wraps urllib with the
supervisor's personal token (read from the environment, never logged). A failing call is reported in the result and the
pass goes on with the next task.
"""
import importlib.util
import sys
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gates = _load('autorunner_gates', 'autogates.py')

STATE = '/api/v1/supervisor/state?project=%s'
DECISION = '/api/v1/supervisor/tasks/%d/plan-decision?project=%s'
VERDICT_TO_DECISION = {'approve': 'approve', 'changes': 'changes', 'person': 'escalate'}


def plan_step(project, http, read, base_ref):
    """-> list of {"task", "verdict", "decision", "status", "error"} for the tasks it acted on (none for a quiet pass)."""
    status, state = http('GET', STATE % quote(project, safe=''))
    if status != 200 or not isinstance(state, dict):
        return [{'task': None, 'verdict': None, 'decision': None, 'status': status, 'error': 'could not read the state'}]
    loaded = gates.load_policy(read, base_ref)
    out = []
    for task in state.get('tasks', []):
        if task.get('status') != 'plan_review' or task.get('plan_decided'):
            continue
        gate = gates.plan_gate(task, loaded['policy'])
        if loaded['error']:
            gate['reasons'] = [loaded['error']] + gate['reasons']
        decision = VERDICT_TO_DECISION[gate['verdict']]
        code, body = http('POST', DECISION % (task['number'], quote(project, safe='')),
                          {'decision': decision, 'reasons': gate['reasons'], 'policy_sha': loaded['sha']})
        ok = code == 200 and isinstance(body, dict)
        out.append({'task': task['number'], 'verdict': gate['verdict'],
                    'decision': body.get('decision') if ok else None, 'status': code,
                    'error': None if ok else (body.get('error') if isinstance(body, dict) else None) or 'refused'})
    return out
