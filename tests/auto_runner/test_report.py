"""auto-runner plugin: read-only status and log reports, and the command files."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner'
BIN = PLUGIN / 'bin'


def _mod(name, file):
    spec = importlib.util.spec_from_file_location(name, BIN / file)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


rep = _mod('autorunner_report_t', 'auto_report.py')

QUEUE = """# Queue

- [x] **A1 First thing.** done long ago and finished with everything it needed to do.
  *Acceptance:* test_a passes.
  *Status:* done abc1234567
- [ ] **A2 Second thing.** is being worked on at the moment by the runner right now.
  *Acceptance:* test_b passes.
  *Status:* running
- [ ] **A3 Third thing.** needs an answer before anybody can carry on with the work.
  *Acceptance:* test_c passes.
  *Status:* blocked Which payment provider do we use?
- [ ] **A4 Fourth thing.** simply waiting its turn in the queue for the runner to pick it up.
  *Acceptance:* test_d passes.

## Needs a person
- deploy
"""


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}


def make_project(tmp_path, queue=QUEUE, runs=None, logs=()):
    (tmp_path / '.auto' / 'logs').mkdir(parents=True)
    (tmp_path / '.claude').mkdir()
    (tmp_path / '.claude' / 'auto.json').write_text(json.dumps({'test_command': 'pytest', 'base_branch': 'main'}), encoding='utf-8')
    if queue is not None:
        (tmp_path / '.auto' / 'queue.md').write_text(queue, encoding='utf-8')
    if runs is not None:
        (tmp_path / '.auto' / 'runs.jsonl').write_text('\n'.join(json.dumps(r) for r in runs) + '\n', encoding='utf-8')
    for name, text in logs:
        (tmp_path / '.auto' / 'logs' / name).write_text(text, encoding='utf-8')
    return tmp_path


RUNS = [
    {'time': '2026-10-22T10:00:00', 'item': 'A1', 'result': 'done', 'commit': 'abc1234567', 'duration_s': 60.0, 'cost_usd': 1.25},
    {'time': '2026-10-22T11:00:00', 'item': 'A2', 'result': 'unfinished', 'commit': None, 'duration_s': 30.0, 'cost_usd': None},
]
LOGS = [('20261022-1000-iter1.log', 'l1\nfirst log end\n'), ('20261022-1000-iter2.log', 'a\nb\nc\nd\ne\nf\nsecond log end\n')]


def test_status_counts_running_blocked_and_spend(tmp_path):
    p = make_project(tmp_path, runs=RUNS)
    out = rep.status(p)
    assert '1 queued' in out and '1 running' in out and '1 blocked' in out and '1 done' in out
    assert 'Running: A2' in out
    assert 'BLOCKED A3: Which payment provider do we use?' in out
    assert 'Last result: A2 unfinished' in out
    assert 'Recorded spend: $1.25 over 1 iteration(s); 1 iteration(s) did not report a cost' in out
    assert 'git -C' in out and 'log main..HEAD --oneline' in out


def test_status_with_empty_history(tmp_path):
    p = make_project(tmp_path, queue=QUEUE.replace('  *Status:* running\n', ''))
    out = rep.status(p)
    assert 'Last result: none yet' in out
    assert 'Recorded spend: $0.00 over 0 iteration(s)' in out
    assert 'Running: nothing' in out


def test_status_without_queue_file(tmp_path):
    p = make_project(tmp_path, queue=None)
    assert 'No queue file' in rep.status(p)


def test_status_says_unverified_without_tests(tmp_path):
    p = make_project(tmp_path)
    (p / '.claude' / 'auto.json').write_text(json.dumps({'check_only': True}), encoding='utf-8')
    assert 'unverified: no tests' in rep.status(p)


def test_log_lists_latest_first_with_cost_and_tail(tmp_path):
    p = make_project(tmp_path, runs=RUNS, logs=LOGS)
    out = rep.log(p, n=5)
    assert out.index('A2') < out.index('A1')
    assert 'cost not reported' in out and '$1.25' in out
    assert 'commit none' in out and 'commit abc1234567' in out
    assert 'second log end' in out and 'first log end' in out
    assert '    a' not in out.split('A1')[0]                 # only the last five lines of the long log
    assert 'git -C' in out


def test_log_limits_n(tmp_path):
    p = make_project(tmp_path, runs=RUNS, logs=LOGS)
    out = rep.log(p, n=1)
    assert 'A2' in out and 'A1' not in out


def test_log_full_prints_whole_log(tmp_path):
    p = make_project(tmp_path, runs=RUNS, logs=LOGS)
    assert rep.log(p, full=1) == LOGS[1][1]
    assert rep.log(p, full=2) == LOGS[0][1]
    assert 'No log number 3' in rep.log(p, full=3)


def test_log_with_empty_history(tmp_path):
    p = make_project(tmp_path)
    out = rep.log(p)
    assert 'No iterations recorded yet' in out and 'git -C' in out


def test_log_tolerates_bad_lines_and_missing_log_file(tmp_path):
    p = make_project(tmp_path, runs=RUNS)
    with (p / '.auto' / 'runs.jsonl').open('a', encoding='utf-8') as f:
        f.write('not json\n')
    assert '(no log file)' in rep.log(p)


def test_reports_change_no_file(tmp_path):
    p = make_project(tmp_path, runs=RUNS, logs=LOGS)
    before = tree(p)
    rep.status(p)
    rep.log(p)
    rep.log(p, full=1)
    assert rep.main(['status', '--project', str(p)]) == 0
    assert rep.main(['log', '--project', str(p), '-n', '2']) == 0
    assert tree(p) == before


COMMANDS = sorted((PLUGIN / 'commands').glob('*.md'))


def test_expected_commands_exist():
    assert {c.name for c in COMMANDS} == {'auto.md', 'init.md', 'status.md', 'log.md', 'resume.md', 'setup.md', 'doctor.md', 'join.md', 'update.md'}


@pytest.mark.parametrize('cmd', COMMANDS, ids=lambda c: c.name)
def test_commands_are_project_neutral(cmd):
    text = cmd.read_text(encoding='utf-8').lower()
    for word in ('saleenable', 'mission-control', 'mission_control', 'venv', 'siva'):
        assert word not in text, f'{cmd.name} mentions {word}'
    assert text.startswith('---\ndescription:')


@pytest.mark.parametrize('cmd', COMMANDS, ids=lambda c: c.name)
def test_commands_call_only_existing_scripts(cmd):
    text = cmd.read_text(encoding='utf-8')
    for script in re.findall(r'\$\{CLAUDE_PLUGIN_ROOT\}/bin/([A-Za-z_]+\.py)', text):
        assert (BIN / script).exists(), script


def test_report_commands_call_the_report_script_only():
    for name, sub in (('status.md', 'status'), ('log.md', 'log')):
        text = (PLUGIN / 'commands' / name).read_text(encoding='utf-8')
        assert f'auto_report.py" {sub}' in text
        assert 'auto_run.py' not in text and '--apply' not in text
