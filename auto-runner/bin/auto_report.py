"""
Read-only reports for the auto-runner: what the queue looks like and what the last iterations did. Changes no file.

    python auto_report.py status [--project DIR]
    python auto_report.py log [-n 5] [--full N] [--project DIR]

status: counts by state, the running item, the last result, blocked items with their question, total recorded spend.
log:    the last n iterations from `.auto/runs.jsonl` (item, result, commit, cost or "cost not reported") with the tail of each log;
        `--full N` prints the whole log of the Nth most recent iteration (1 = latest).
Both end with the git command that shows the same work. Cost is only what the CLI reported; it is never estimated.
"""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUTO_DIR = '.auto'
TAIL_LINES = 5


def _load(name, file):
    # By path: bin/queue.py must never shadow the standard library's `queue`.
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


autocfg = _load('autoreport_cfg', 'autocfg.py')
queue = _load('autoreport_queue', 'queue.py')


def read_runs(project):
    """-> list of run dicts, oldest first. Unreadable lines are skipped, a missing file is an empty history."""
    path = Path(project) / AUTO_DIR / 'runs.jsonl'
    if not path.exists():
        return []
    runs = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            runs.append(entry)
    return runs


def _logs(project):
    d = Path(project) / AUTO_DIR / 'logs'
    return sorted(d.glob('*.log')) if d.exists() else []


def _cost(entry):
    c = entry.get('cost_usd')
    return f'${c:.2f}' if isinstance(c, (int, float)) and not isinstance(c, bool) else 'cost not reported'


def _git_hint(project, cfg):
    wt = Path(project).parent / (Path(project).name + cfg['worktree_suffix'])
    try:
        base = autocfg.detect_base_branch(Path(project), cfg)
    except Exception:
        base = '<base branch>'
    return f'git -C "{wt}" log {base}..HEAD --oneline    (and: git -C "{wt}" diff {base}...HEAD)'


def status(project):
    project = Path(project)
    cfg = autocfg.load(project)
    qpath = project / cfg['queue_file']
    lines = []
    if not qpath.exists():
        return f'No queue file at {cfg["queue_file"]}. Run the init command first.\n'
    rows = queue.statuses(qpath.read_text(encoding='utf-8'))
    counts = {}
    for _, state, _ in rows:
        counts[state] = counts.get(state, 0) + 1
    order = [s for s in (*queue.STATES, 'unknown') if s in counts]
    lines.append('Queue: ' + (', '.join(f'{counts[s]} {s}' for s in order) if rows else 'no items'))
    running = [i for i, s, _ in rows if s == 'running']
    lines.append('Running: ' + (', '.join(running) if running else 'nothing'))
    for item_id, state, detail in rows:
        if state in ('blocked', 'rejected'):
            lines.append(f'  {state.upper()} {item_id}: {detail or "(no reason recorded)"}')
    runs = read_runs(project)
    if runs:
        last = runs[-1]
        lines.append(f'Last result: {last.get("item")} {last.get("result")} at {last.get("time")}'
                     + (f' (commit {last["commit"]})' if last.get('commit') else ''))
    else:
        lines.append('Last result: none yet')
    reported = [r['cost_usd'] for r in runs if isinstance(r.get('cost_usd'), (int, float)) and not isinstance(r.get('cost_usd'), bool)]
    missing = len(runs) - len(reported)
    lines.append(f'Recorded spend: ${sum(reported):.2f} over {len(reported)} iteration(s)'
                 + (f'; {missing} iteration(s) did not report a cost' if missing else ''))
    if cfg['check_only'] or not cfg['test_command']:
        lines.append('unverified: no tests (this project has no test command)')
    lines.append('See the same work in git: ' + _git_hint(project, cfg))
    return '\n'.join(lines) + '\n'


def log(project, n=5, full=None):
    project = Path(project)
    cfg = autocfg.load(project)
    runs, logs = read_runs(project), _logs(project)
    # Logs and run lines are written together, one each per iteration; pair them from the newest end.
    if full is not None:
        if full < 1 or full > len(logs):
            return f'No log number {full}: {len(logs)} log file(s) exist.\n'
        return logs[-full].read_text(encoding='utf-8', errors='replace')
    if not runs:
        return 'No iterations recorded yet.\n' + 'See the work in git: ' + _git_hint(project, cfg) + '\n'
    out = []
    for k, entry in enumerate(reversed(runs[-max(n, 1):]), start=1):
        out.append(f'{entry.get("time")}  {entry.get("item")}  {entry.get("result")}  '
                   f'commit {entry.get("commit") or "none"}  {_cost(entry)}')
        if k <= len(logs):
            tail = logs[-k].read_text(encoding='utf-8', errors='replace').splitlines()[-TAIL_LINES:]
            out.extend('    ' + t for t in tail)
        else:
            out.append('    (no log file)')
    out.append('See the same work in git: ' + _git_hint(project, cfg))
    return '\n'.join(out) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=['status', 'log'])
    ap.add_argument('--project', default=os.environ.get('CLAUDE_PROJECT_DIR') or '.')
    ap.add_argument('-n', type=int, default=5)
    ap.add_argument('--full', type=int, metavar='N')
    a = ap.parse_args(argv)
    sys.stdout.write(status(a.project) if a.command == 'status' else log(a.project, a.n, a.full))
    return 0


if __name__ == '__main__':
    sys.exit(main())
