# Task 12: the plugin's commands never crash on characters the console can't show

## Intent
On Windows the console encoding is cp1252. `python autotasks.py next` printed task 6, then crashed with
`UnicodeEncodeError: 'charmap' codec can't encode character '→'` while printing its plan. Any command that prints
task, plan, blocker or log text can hit this. Outcome: every `auto-runner/bin` command prints such text without
crashing, on any console encoding. Out of scope: what is sent to the central service (already UTF-8 JSON), and the
child processes the runner reads, which already get `PYTHONIOENCODING=utf-8` (`auto_run.child_env`).

## Key decisions
1. **Where to fix.** Options: (a) one helper called by every entry point; (b) wrap only the prints known to fail.
   **Choice (a):** the same text reaches many commands, and (b) would miss the next one.
2. **How.** `sys.stdout` / `sys.stderr` `.reconfigure(encoding='utf-8', errors='replace')` when the stream supports it.
   Claude reads these commands through a pipe and decodes UTF-8, so the characters arrive intact; a person's cp1252
   console shows a few wrong glyphs instead of a crash. Streams without `reconfigure` (a test's StringIO) are left alone.
3. **Where the helper lives:** `autosafe.py`, which every runner script already loads or can load with the existing
   `_load` pattern; it stays standard library only.

## End to end
No data, config or service change. Docs: `auto-runner/README.md` (the module table). Rollback: remove the call.

## Slices
- **Slice 1 Task commands print any task.** `autotasks.py` (next, list, plan, start, record) prints tasks with "→" on cp1252.
- **Slice 2 Every command.** All `auto-runner/bin` entry points call the helper, and a test keeps it that way.

## Journeys
- **J1 Next task with an arrow.** Slice 1. Roles: developer.
  1. On Windows, without `PYTHONIOENCODING`, run `python auto-runner/bin/autotasks.py next` for a task whose plan contains "→".
  *Expect:* the task and its plan are printed and the command exits 0.

## Libraries
Nothing new: the standard library (`io.TextIOWrapper.reconfigure`, Python 3.7+).

| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Safe console output | `TextIOWrapper.reconfigure` (stdlib) | free | PSF | reuse | built in; no package needed |

## Queue items

- [ ] **W1 UTF-8 console output for the task commands.** In `auto-runner/bin/autosafe.py` add `utf8_console(streams=None)`: for `sys.stdout` and `sys.stderr` (or the given streams), call `reconfigure(encoding='utf-8', errors='replace')` when the stream has `reconfigure`; skip any stream without it; never raise. Call it as the first line of the `if __name__ == '__main__':` block of `auto-runner/bin/autotasks.py`. Nothing sent to the central service changes. Update the `autosafe.py` and `autotasks.py` rows of `auto-runner/README.md`.
  *Slice:* 1 Task commands print any task
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_encoding.py` passes with new tests: the test writes a small driver script to `tmp_path` that loads `auto-runner/bin/autotasks.py`, replaces its central transport with `tests/auto_runner/fake_central.py`'s in-process `send` (as `test_central_tasks.py` does), calls `autosafe.utf8_console()` exactly as the `__main__` block does, then calls `main([...])`. It runs that driver with `subprocess` and `PYTHONIOENCODING=cp1252` (and once with the variable removed): `list` with a task titled `A → B`, and `next` for a task whose plan contains `→`, both exit 0, and the UTF-8-decoded output contains the title and the plan; `utf8_console` on an `io.TextIOWrapper(io.BytesIO(), encoding='cp1252')` lets `print('→')` succeed, and on an `io.StringIO` it does nothing and does not raise; the docs check passes; full `python -m pytest -q` green.
- [ ] **W2 Every bin command uses UTF-8 console output.** Call `autosafe.utf8_console()` first in the `if __name__ == '__main__':` block of every other script in `auto-runner/bin/` that has one (`auto_init.py`, `auto_join.py`, `auto_report.py`, `auto_run.py`, `autoblock.py`, `autocentral.py`, `autochange.py`, `autodeliver.py`, `autodocs.py`, `autoeval.py`, `autohook.py`, `automail.py`, `autoplan.py`, `autosetup.py`, `autospec.py`, `autostale.py`, `autoupdate.py`, `autowatch.py`, `queue.py`), loading `autosafe` with that script's existing `_load(name, file)` helper. A script without one copies `autotasks.py`'s `_load` helper. The exception is `queue.py`: it must never put its folder on `sys.path`, because it shadows the stdlib `queue`, so it gets a plain inline guard in its `__main__` block instead (`for s in (sys.stdout, sys.stderr): getattr(s, 'reconfigure', None) and s.reconfigure(encoding='utf-8', errors='replace')`). For `autohook.py`, keep its rule that it always exits 0. Update `auto-runner/README.md` (one line: every command writes UTF-8 to its console).
  *Slice:* 2 Every command
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_encoding.py` passes with a new test that finds every `auto-runner/bin/*.py` containing `if __name__ == '__main__':` and asserts the block calls `utf8_console()` before anything else (`queue.py`: the inline `reconfigure` guard), and a test that runs `auto_report.py status --project <tmp project>` with `PYTHONIOENCODING=cp1252` on a queue item titled `A → B` and gets exit 0 and the title back; the docs check passes; full suite green.

## Needs a person
Nothing. Assumed: Claude reads these commands' output through a pipe as UTF-8 (true for the Bash tool).

## Verification
`python -m pytest -q`, all green. J1 on a real Windows console is unverified until someone runs it.

## Spec check
Round 1 (3 findings, all fixed): the W1 test now names its mechanism, a driver script with `fake_central.send` run
in a subprocess under cp1252 and with the variable unset. It covers `next` with an arrow in the plan, matching J1. W2
says what to do in scripts without `_load`, and gives `queue.py` an inline guard, because it shadows the stdlib `queue`.
The fixes were not re-checked: one round, to keep the queue moving. The reviewer itself crashed on this bug once
(printing "→"), which is fixed by this task.
