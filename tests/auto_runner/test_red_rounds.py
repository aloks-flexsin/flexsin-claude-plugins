"""auto-runner plugin: a red suite reopens the item and gets fix rounds (with the failing output) before the run stops with a
blocker; fast checks before the commit; the runner restores an item's handover stash itself."""
from pathlib import Path

from tests.auto_runner.test_runner import GREEN, ar, events, fake_agent, project, project_statuses, set_cfg, sh, \
    worktree  # noqa: F401  (project is a fixture)

FAILING = 'FAILED tests/e2e/test_bell.py::test_correction_reaches_bell - AssertionError: bell is empty\n1 failed, 40 passed'


def suite(*codes):
    """Fake independent suite: the given exit codes in order, then green; red output names the failing e2e test."""
    seq = list(codes)

    def run(command, cwd):
        code = seq.pop(0) if seq else 0
        return code, (FAILING if code else '41 passed')
    return run


def fixer(calls=None):
    """Like fake_agent, but every attempt writes something new, so a fix round has a change to commit."""
    n = [0]

    def claude(cmd, cwd):
        n[0] += 1
        if calls is not None:
            calls.append(cmd)
        cwd = Path(cwd)
        item = cmd[2].split('Do ONLY item ', 1)[1].split()[0]
        q = cwd / '.auto' / 'queue.md'
        q.write_text(q.read_text(encoding='utf-8').replace(f'- [ ] **{item} ', f'- [x] **{item} ', 1), encoding='utf-8')
        (cwd / f'{item}.txt').write_text(f'attempt {n[0]}', encoding='utf-8')
        sh(cwd, 'add', '-A', '--', f'{item}.txt')
        sh(cwd, '-c', 'user.email=t@example.com', '-c', 'user.name=T', 'commit', '-q', '-m', f'{item} attempt {n[0]}')
        return 0, 'done'
    return claude


def queue_text(project):
    return (project / '.auto' / 'queue.md').read_text(encoding='utf-8')


def test_a_red_suite_reopens_the_item_and_the_fix_round_gets_the_failure(project, capsys):
    calls = []
    code = ar.run(project, max_iterations=2, claude=fixer(calls), tests=suite(1))
    assert code == 0
    first, fix = calls[0][2], calls[1][2]
    assert 'Do ONLY item A1' in first and 'Do ONLY item A1' in fix          # the same item again, not the next one
    assert 'fix round 1 of 2' in fix and 'test_correction_reaches_bell' in fix and 'do not weaken' in fix
    st = project_statuses(project)
    assert st['A1'][0] == 'done' and st['A2'][0] == 'done'                  # the extra round did not use up A2's iteration
    assert 'reopened it for fix round 1 of 2' in capsys.readouterr().out


def test_after_the_last_fix_round_it_raises_a_blocker_and_stops_with_the_item_unticked(project, capsys):
    set_cfg(project, test_command=GREEN, review={'max_fix_rounds': 1})
    code = ar.run(project, max_iterations=3, claude=fixer(), tests=suite(1, 1, 1))
    assert code == 1 and events(project)[-1]['detail']['reason'] == 'red'
    assert '- [ ] **A1 ' in queue_text(project)                             # never left ticked on a red suite
    assert project_statuses(project)['A1'][0] == 'blocked'
    blockers = ar.autoblock.Store(project / '.auto').all()[0]
    assert len(blockers) == 1 and 'still red after A1' in blockers[0]['question']
    assert 'test_correction_reaches_bell' in blockers[0]['question']
    assert 'raised a blocker and stopping' in capsys.readouterr().out


def test_with_no_fix_rounds_a_red_suite_still_unticks_the_item(project):
    set_cfg(project, test_command=GREEN, review={'max_fix_rounds': 0})
    assert ar.run(project, max_iterations=2, claude=fixer(), tests=suite(1)) == 1
    assert '- [ ] **A1 ' in queue_text(project) and '- [x] **A1 ' not in queue_text(project)
    assert '- [x] **A1 ' not in (worktree(project) / '.auto' / 'queue.md').read_text(encoding='utf-8')


def test_uncommitted_changes_on_a_red_suite_are_handed_to_the_fix_round(project):
    seen = []
    inner = fixer()

    def agent(cmd, cwd):
        draft = Path(cwd) / 'draft.txt'
        seen.append(draft.exists())
        if len(seen) == 1:
            draft.write_text('half', encoding='utf-8')                     # left uncommitted when the suite goes red
        else:
            draft.unlink(missing_ok=True)                                   # the fix round folds it into its work
        return inner(cmd, cwd)
    assert ar.run(project, max_iterations=2, claude=agent, tests=suite(1)) == 0
    assert seen[:2] == [False, True]                                        # the draft is back for the fix round


# --- fast checks before the commit ----------------------------------------------------------------------------------------

def test_the_prompt_asks_for_the_projects_fast_checks_and_end_to_end_tests():
    cfg = {'test_command': 'npm test', 'check_command': 'npm run lint && npm run typecheck', 'check_only': False,
           'queue_file': '.auto/queue.md', 'rules_files': []}
    text = ar.build_prompt(cfg, '.auto/queue.md', 'B7')
    assert '`npm run lint && npm run typecheck` passing' in text and 'end-to-end tests of every service' in text
    assert 'all run before you commit' in text and 'a red suite reopens the item' in text
    generic = ar.build_prompt({**cfg, 'check_command': ''}, '.auto/queue.md', 'B7')
    assert "lint and type checks passing if it has them" in generic


def test_the_check_command_is_an_allowed_tool(project):
    set_cfg(project, test_command=GREEN, check_command='npm run lint')
    calls = []
    assert ar.run(project, max_iterations=1, claude=fake_agent(calls=calls)) == 0
    assert 'Bash(npm run lint:*)' in calls[0]


# --- the runner restores a handover itself --------------------------------------------------------------------------------

def test_a_handover_stash_is_restored_before_the_items_next_attempt(project, capsys):
    assert ar.run(project, max_iterations=1, claude=fake_agent()) == 0       # A1 done, the worktree exists
    wt = worktree(project)
    (wt / 'A2-part.txt').write_text('earlier work', encoding='utf-8')
    sh(wt, 'stash', 'push', '--include-untracked', '-m', 'auto-runner: A2 unfinished at a usage limit (20261118-0821)')
    found, calls = [], []
    inner = fake_agent(calls=calls)

    def agent(cmd, cwd):
        found.append((Path(cwd) / 'A2-part.txt').read_text(encoding='utf-8'))
        (Path(cwd) / 'A2-part.txt').unlink()                                 # the agent folds it into its own work
        return inner(cmd, cwd)
    capsys.readouterr()
    assert ar.run(project, max_iterations=1, claude=agent, allow_dirty=True) == 0
    assert found == ['earlier work']
    assert 'has been put back in the working tree' in calls[0][2] and 'do not start over' in calls[0][2]
    assert 'A2 unfinished' not in ar.git(wt, 'stash', 'list')
    assert 'restored the handover of A2' in capsys.readouterr().out


def test_another_items_stash_is_left_alone(project):
    assert ar.run(project, max_iterations=1, claude=fake_agent()) == 0
    wt = worktree(project)
    (wt / 'other.txt').write_text('x', encoding='utf-8')
    sh(wt, 'stash', 'push', '--include-untracked', '-m', 'auto-runner: Z9 unfinished at a usage limit (x)')
    calls = []
    assert ar.run(project, max_iterations=1, claude=fake_agent(calls=calls), allow_dirty=True) == 0
    assert 'Z9 unfinished' in ar.git(wt, 'stash', 'list') and 'put back' not in calls[0][2]
