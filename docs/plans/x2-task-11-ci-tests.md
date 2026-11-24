# Task 11: run the test suite on every pull request

## Intent
Only the Vercel preview check runs on pull requests; the repository has no GitHub Actions workflow. On 2026-11-24 a
test that only works on Python 3.13 (`Path.read_text(newline=...)`) was merged in #24 and made `python -m pytest -q`
red on Python 3.12 for everyone until #29. Outcome: every pull request to `main` and every push to `main` shows a
`tests` check that runs the whole suite on Python 3.12, so a red suite is visible before merging. Out of scope: making
the check required (branch protection is a repository setting a person changes) and deploying anything.

## Key decisions
1. **Python version:** 3.12, the oldest version the team uses (this machine has 3.12.10). The test fails there first.
2. **What it installs:** `requirements-dev.txt` (pytest, playwright, flask) and `auto-runner/central/requirements.txt`.
   Playwright's browsers are installed only if a test really launches one. The browser tests checked so far use a
   fake browser (`fake_browser` in `tests/auto_runner/test_browser_stage.py`). The 2 local skips are POSIX file-mode
   tests that will run on Linux. The item checks this and adds `python -m playwright install --with-deps chromium`
   only when a test needs it.
3. **No secrets, no network beyond installing packages.** The suite uses fakes (`tests/auto_runner/fake_central.py`)
   and never reaches live services. The workflow gets `permissions: contents: read` only.
4. **Speed:** the suite takes about 11 minutes. Cache pip with `actions/setup-python`'s built-in cache. Cancel an
   older run on the same branch when a new push arrives (`concurrency`).

## End to end
One workflow file and a README line. Nothing deployed. Rollback: delete the file. It is checked by the next pull
request the runner opens, whose `tests` check must pass.

## Slices
- **Slice 1 A tests check on every PR.** A pull request shows `tests`, red when a test fails, green when the suite passes.

## Journeys
- **J1 A PR shows the check.** Slice 1. Roles: developer.
  1. Open any pull request to `main`.
  *Expect:* the checks list shows `tests`, which runs `python -m pytest -q` on Python 3.12 and passes on a green suite.

## Libraries

| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| CI runner | GitHub Actions (https://docs.github.com/actions), `actions/checkout@v4`, `actions/setup-python@v5` | free for this repository's usage (GitHub's included minutes; unverified for private-repo limits) | MIT (the actions) | reuse | the repository is on GitHub already; nothing to host |

## Queue items

- [ ] **G1 GitHub Actions workflow that runs the suite.** Add `.github/workflows/tests.yml`, workflow `name: tests`, one job `tests` on `ubuntu-latest` that runs on `pull_request` to `main` and `push` to `main`. It uses `permissions: contents: read`, a `concurrency` group per ref with `cancel-in-progress: true`, `actions/checkout@v4`, `actions/setup-python@v5` with `python-version: '3.12'` and `cache: pip`, installs `requirements-dev.txt` and `auto-runner/central/requirements.txt` with pip, then runs `python -m pytest -q`. First check whether any test launches a real browser (search `tests/` for `sync_playwright`, `.launch(`, `chromium`): if one does, add `python -m playwright install --with-deps chromium` before the tests; if none does, leave it out and say so in the commit message. It sets no secrets and no environment values beyond what the suite needs. Add a test `tests/auto_runner/test_ci_workflow.py` that reads the file as text and asserts those triggers, the Python version, the read-only permissions, the pytest command, and that it contains no `secrets.`. Add a short "Continuous integration" paragraph to `auto-runner/README.md`.
  *Slice:* 1 A tests check on every PR
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_ci_workflow.py` passes; the docs check passes; full `python -m pytest -q` green. The pull request the runner opens for this task shows a `tests` check (checked by a person or the Supervisor on the PR; unverified until then).

## Needs a person
- Making `tests` a required check on `main` (branch protection, a repository setting).
- Assumed: GitHub Actions is enabled for the repository and its minutes are not limited for this use.

## Verification
The new test pins the workflow's content locally. The real proof is the `tests` check on this task's own pull request.

## Spec check
No findings (one round).
