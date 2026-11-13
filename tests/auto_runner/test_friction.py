"""auto-runner plugin: friction found in real runs. A usage-limit stop is recognised (not reported as red, leftovers or no
progress), a reused worktree's merged branch is brought up to the base, an older runner than the installed one refuses to
start, and the run report gives the suite's totals."""
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.auto_runner.test_runner import GREEN, ar, events, fake_agent, project, project_statuses, runs, set_cfg, sh, \
    worktree  # noqa: F401  (project is a fixture)

NOW = datetime(2026, 11, 13, 10, 0).astimezone()


def result_line(text, is_error=True):
    return json.dumps({'type': 'result', 'subtype': 'success', 'is_error': is_error, 'result': text, 'total_cost_usd': 0.1})


# --- recognising the limit ----------------------------------------------------------------------------------------------

def test_the_epoch_form_gives_the_exact_reset():
    epoch = int((NOW + timedelta(hours=2)).timestamp())
    hit = ar.usage_limit(result_line(f'Claude AI usage limit reached|{epoch}'), NOW)
    assert hit is not None and hit['reset'] == NOW + timedelta(hours=2)


@pytest.mark.parametrize('text,hour,minute,day', [
    ("You've hit your limit \u00b7 resets 3pm (Asia/Calcutta)", 15, 0, 13),
    ('5-hour limit reached \u2219 resets 9:30am', 9, 30, 14),            # 9:30 has passed at 10:00: tomorrow
    ('Usage limit reached. Your limit will reset at 23:15', 23, 15, 13),
])
def test_the_clock_form_gives_the_next_such_time(text, hour, minute, day):
    hit = ar.usage_limit(result_line(text), NOW)
    assert hit is not None and (hit['reset'].day, hit['reset'].hour, hit['reset'].minute) == (day, hour, minute)


def test_a_limit_without_a_time_is_still_recognised():
    hit = ar.usage_limit(result_line('Claude AI usage limit reached'), NOW)
    assert hit is not None and hit['reset'] is None


def test_a_successful_result_is_never_a_limit_even_if_the_work_mentions_one():
    transcript = '\n'.join([
        json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': 'raise UsageLimitReached: usage limit reached'}]}}),
        result_line('Added the "usage limit reached" banner; resets at 3pm in the copy.', is_error=False)])
    assert ar.usage_limit(transcript, NOW) is None


def test_a_limit_in_the_last_lines_counts_when_the_cli_left_no_result():
    assert ar.usage_limit('starting\nClaude AI usage limit reached|1700000000\n', NOW) is not None
    assert ar.usage_limit('starting\nall fine\n', NOW) is None


# --- a run that hits the limit ------------------------------------------------------------------------------------------

def limited_agent(calls, then=None, reset_in=timedelta(minutes=5)):
    """First call: leaves an uncommitted file and stops on the usage limit. Later calls: `then` (or the limit again)."""
    def claude(cmd, cwd):
        calls.append(cmd)
        if len(calls) > 1 and then is not None:
            return then(cmd, cwd)
        (Path(cwd) / 'half-done.txt').write_text('partial', encoding='utf-8')
        epoch = int((datetime.now().astimezone() + reset_in).timestamp())
        return 1, result_line(f'Claude AI usage limit reached|{epoch}')
    return claude


def test_a_limit_stops_cleanly_keeps_the_work_and_is_not_called_red(project, capsys):
    calls, suite = [], []
    code = ar.run(project, max_iterations=3, claude=limited_agent(calls),
                  tests=lambda c, cwd: suite.append(c) or (1, 'would be red'))
    assert code == ar.USAGE_LIMIT_EXIT and len(calls) == 1 and suite == []        # no suite run, no "red"
    assert runs(project)[-1]['result'] == 'usage_limit'
    assert events(project)[-1]['detail'] == {'exit': ar.USAGE_LIMIT_EXIT, 'reason': 'usage_limit'}
    st = project_statuses(project)['A1']
    assert st[0] == 'queued' and 'usage limit' in st[1]
    wt = worktree(project)
    assert not (wt / 'half-done.txt').exists()
    assert 'A1 unfinished at a usage limit' in ar.git(wt, 'stash', 'list')
    out = capsys.readouterr().out
    assert 'usage limit reached during A1' in out and 'stash list' in out
    assert 'RED' not in out and 'leftovers' not in out.lower()


def test_with_waiting_on_the_run_sleeps_until_the_reset_and_carries_on(project):
    set_cfg(project, test_command=GREEN, usage_limit={'wait': True, 'max_wait_minutes': 30})
    calls, slept = [], []
    code = ar.run(project, max_iterations=1, claude=limited_agent(calls, then=fake_agent()),
                  sleep=lambda s: slept.append(s), allow_dirty=True)
    assert code == 0 and len(calls) == 2                       # the interrupted try did not use up the one iteration
    assert project_statuses(project)['A1'][0] == 'done'
    assert 300 <= sum(slept) <= 420 and max(slept) <= 30        # about the 5 minutes to the reset, plus one, in steps


def test_a_reset_beyond_the_longest_wait_stops_instead(project):
    set_cfg(project, test_command=GREEN, usage_limit={'wait': True, 'max_wait_minutes': 30})
    calls, slept = [], []
    code = ar.run(project, max_iterations=2, claude=limited_agent(calls, reset_in=timedelta(hours=3)),
                  sleep=lambda s: slept.append(s), allow_dirty=True)
    assert code == ar.USAGE_LIMIT_EXIT and slept == [] and len(calls) == 1


def test_a_stop_file_ends_the_wait(project):
    set_cfg(project, test_command=GREEN, usage_limit={'wait': True})
    stop = project / '.auto' / 'STOP'
    slept = []

    def sleep(s):
        slept.append(s)
        stop.write_text('', encoding='utf-8')

    code = ar.run(project, max_iterations=1, claude=limited_agent([], then=fake_agent()), sleep=sleep, allow_dirty=True)
    assert code == 0 and len(slept) == 1


def test_usage_limit_is_a_known_config_section(project):
    set_cfg(project, test_command=GREEN, usage_limit={'wait': True, 'max_wait_minutes': 90})
    assert ar.autocfg.load(project)['usage_limit'] == {'wait': True, 'max_wait_minutes': 90}


# --- a reused worktree whose branch was merged --------------------------------------------------------------------------

def commit_on(cwd, name):
    (Path(cwd) / name).write_text(name, encoding='utf-8')
    sh(cwd, 'add', name)
    sh(cwd, '-c', 'user.email=t@example.com', '-c', 'user.name=T', 'commit', '-q', '-m', name)


def test_a_merged_branch_is_moved_up_to_the_base(project, capsys):
    assert ar.run(project, max_iterations=1, claude=fake_agent()) == 0           # A1 done on the run branch
    wt = worktree(project)
    branch = ar.git(wt, 'branch', '--show-current')
    sh(project, 'merge', '-q', '--ff-only', branch)                              # a person merges it ...
    commit_on(project, 'slice38.txt')                                            # ... and main moves on
    capsys.readouterr()
    assert ar.run(project, max_iterations=1, claude=fake_agent(), allow_dirty=True) == 0
    assert (wt / 'slice38.txt').exists()
    assert 'already merged into main: moved it up 1 commit(s)' in capsys.readouterr().out


def test_a_branch_with_unmerged_work_is_left_alone_with_a_warning(project, capsys):
    assert ar.run(project, max_iterations=1, claude=fake_agent()) == 0
    wt = worktree(project)
    commit_on(project, 'other.txt')                                              # main moves on, the branch is not merged
    head = ar.git(wt, 'rev-parse', 'HEAD')
    capsys.readouterr()
    assert ar.run(project, max_iterations=1, claude=fake_agent(), allow_dirty=True) == 0
    out = capsys.readouterr().out
    assert 'warning: main has 1 commit(s)' in out and not (wt / 'other.txt').exists()
    assert ar.git(wt, 'rev-list', '--count', f'{head}..HEAD') in ('0', '1')      # only this run's own work was added


# --- the installed version wins -----------------------------------------------------------------------------------------

def install_record(version, path='C:/plugins/auto-runner/' + 'x'):
    cfg = Path(os.environ['CLAUDE_CONFIG_DIR']) / 'plugins'
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / 'installed_plugins.json').write_text(json.dumps({'version': 2, 'plugins': {
        'auto-runner@flexsin-claude-plugins': [{'scope': 'user', 'installPath': path, 'version': version}]}}),
        encoding='utf-8')


def test_an_older_runner_than_the_installed_one_refuses_and_names_the_right_path(project):
    install_record('99.0.0', 'C:/cache/auto-runner/99.0.0')
    calls = []
    with pytest.raises(ar.RunnerError, match=r'but 99\.0\.0 is installed') as e:
        ar.run(project, max_iterations=1, claude=fake_agent(calls=calls))
    assert 'auto_run.py' in str(e.value) and '99.0.0' in str(e.value) and calls == []


@pytest.mark.parametrize('version', ['0.0.1', None])
def test_the_same_or_an_older_install_or_none_lets_it_run(project, version):
    if version:
        install_record(version)
    assert ar.run(project, max_iterations=1, claude=fake_agent()) == 0


# --- the suite's totals in the report -----------------------------------------------------------------------------------

@pytest.mark.parametrize('output,expect', [
    ('...\n=== 1241 passed, 1 skipped in 711.84s (0:11:51) ===\n', '1241 passed, 1 skipped in 711.84s (0:11:51)'),
    ('Tests:       2 failed, 40 passed, 42 total\nTime: 3s\n', 'Tests:       2 failed, 40 passed, 42 total'),
    ('  12 passing (40ms)\n', '12 passing (40ms)'),
    ('no totals here\n', ''),
])
def test_suite_summary(output, expect):
    assert ar.suite_summary(output) == expect


def test_the_run_report_ends_with_the_suites_totals(project, capsys):
    assert ar.run(project, max_iterations=1, claude=fake_agent(),
                  tests=lambda c, cwd: (0, 'collected 8 items\n8 passed, 2 skipped in 1.2s\n')) == 0
    assert 'suite at the last independent run: 8 passed, 2 skipped in 1.2s' in capsys.readouterr().out
