"""auto-runner plugin (0.12.1): a budget stop becomes a handover, a soft cut-off at the usage limit is a limit, waiting retries
early and ends as soon as another lane on the account gets through, and a stale lock is cleared."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from tests.auto_runner.test_friction import limited_agent, result_line
from tests.auto_runner.test_runner import GREEN, ar, fake_agent, project, project_statuses, runs, set_cfg, \
    worktree  # noqa: F401  (project is a fixture)

BUDGET = result_line('Exceeded maximum budget ($5.00) for this session')


# --- a budget stop is handed over ----------------------------------------------------------------------------------------

def test_a_budget_stop_hands_the_work_to_the_next_attempt(project, capsys):
    calls, seen = [], []
    finish = fake_agent()

    def agent(cmd, cwd):
        calls.append(cmd)
        draft = Path(cwd) / 'draft.txt'
        seen.append(draft.exists())
        if len(calls) == 1:
            draft.write_text('nearly there', encoding='utf-8')
            return 1, BUDGET
        draft.unlink(missing_ok=True)
        return finish(cmd, cwd)
    assert ar.run(project, max_iterations=1, claude=agent) == 0
    assert seen == [False, True] and 'put back in the working tree' in calls[1][2]
    assert [r['result'] for r in runs(project)] == ['budget', 'done'] and project_statuses(project)['A1'][0] == 'done'
    assert 'budget of A1 ran out before it finished' in capsys.readouterr().out


def test_the_agent_is_told_its_budget(project):
    calls = []
    assert ar.run(project, max_iterations=1, claude=fake_agent(calls=calls), max_budget_usd=3.5) == 0
    assert 'budget of $3.5' in calls[0][2] and 'nine tenths' in calls[0][2]


def test_an_item_that_keeps_running_out_of_budget_stops_after_its_handovers(project):
    calls = []

    def agent(cmd, cwd):
        calls.append(cmd)
        (Path(cwd) / f'draft{len(calls)}.txt').write_text('more', encoding='utf-8')
        return 1, BUDGET
    code = ar.run(project, max_iterations=1, claude=agent)
    assert code == 1 and len(calls) == ar.MAX_BUDGET_HANDOVERS + 1
    assert runs(project)[-1]['result'] == 'leftovers'


def test_a_budget_word_in_the_work_is_not_a_budget_stop():
    assert not ar.budget_stop(result_line('Added the "budget exceeded" banner', is_error=False))
    assert ar.budget_stop(BUDGET) and ar.budget_stop(json.dumps({'type': 'result', 'subtype': 'error_max_budget_usd',
                                                                 'is_error': True, 'result': ''}))


# --- a soft cut-off at the usage limit -----------------------------------------------------------------------------------

def test_a_soft_cut_off_without_a_commit_is_a_usage_limit_not_a_red_suite(project):
    suite = []

    def agent(cmd, cwd):
        return 0, result_line("You've hit your limit · resets 3pm", is_error=False)
    code = ar.run(project, max_iterations=1, claude=agent, tests=lambda c, d: suite.append(c) or (1, 'red'))
    assert code == ar.USAGE_LIMIT_EXIT and suite == [] and runs(project)[-1]['result'] == 'usage_limit'


def test_a_finished_item_that_mentions_limits_is_not_a_cut_off(project):
    inner = fake_agent()

    def agent(cmd, cwd):
        inner(cmd, cwd)                                                     # commits and ticks
        return 0, result_line('Added a "usage limit reached, resets at 3pm" notice.', is_error=False)
    assert ar.run(project, max_iterations=1, claude=agent) == 0
    assert runs(project)[-1]['result'] == 'done'


# --- waiting: early retries and the account shared by lanes ---------------------------------------------------------------

def test_another_lane_getting_through_ends_the_wait(project, capsys):
    set_cfg(project, test_command=GREEN, usage_limit={'wait': True, 'max_wait_minutes': 300})
    slept = []

    def sleep(s):
        slept.append(s)
        ar.mark_account(datetime.now().astimezone() + timedelta(seconds=5))  # the other lane's attempt got through

    finish = fake_agent()

    def then(cmd, cwd):
        (Path(cwd) / 'half-done.txt').unlink(missing_ok=True)
        return finish(cmd, cwd)
    code = ar.run(project, max_iterations=1, claude=limited_agent([], then=then, reset_in=timedelta(hours=3)),
                  sleep=sleep, allow_dirty=True)
    assert code == 0 and slept == [30]
    assert 'another run on this account got through' in capsys.readouterr().out


def test_a_good_attempt_is_written_for_the_other_lanes(project):
    assert ar.run(project, max_iterations=1, claude=fake_agent()) == 0
    assert ar.account_ok_since(datetime.now().astimezone() - timedelta(minutes=1))
    assert not ar.account_ok_since(datetime.now().astimezone() + timedelta(minutes=1))


def test_the_account_file_lives_in_claudes_config_folder():
    assert ar.account_state_path() == Path(os.environ['CLAUDE_CONFIG_DIR']) / 'auto-runner' / 'usage.json'


# --- stale locks ---------------------------------------------------------------------------------------------------------

def dead_pid():
    pid = 4_000_000
    while ar.pid_alive(pid):
        pid += 1
    return pid


def test_a_lock_left_by_a_dead_run_is_cleared(project, capsys):
    (project / '.auto' / 'lock').write_text(str(dead_pid()), encoding='utf-8')
    assert ar.run(project, max_iterations=1, claude=fake_agent()) == 0
    assert 'clearing a stale lock' in capsys.readouterr().out
    assert not (project / '.auto' / 'lock').exists()


def test_an_unreadable_lock_is_still_respected(project):
    (project / '.auto' / 'lock').write_text('not a pid', encoding='utf-8')
    try:
        ar.run(project, max_iterations=1, claude=fake_agent())
    except ar.RunnerError as e:
        assert 'already in progress' in str(e)
    else:
        raise AssertionError('an unreadable lock must not be cleared')


def test_pid_alive():
    assert ar.pid_alive(os.getpid()) and not ar.pid_alive(dead_pid()) and not ar.pid_alive(0)
