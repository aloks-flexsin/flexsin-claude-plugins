# auto-runner

A Claude Code plugin. You plan a task with `/auto`, the plan becomes queue items, and a runner works through them unattended in a separate git worktree:
one item per iteration, an independent test run after each, one commit per green item. Nothing is pushed, deployed or merged.

Status: **built and tested with a fake `claude` command; never run on a real second project or live.** Treat it as unverified until it has.
The foundations added later (event ledger, blockers, delivery, staleness, evaluation harness; ADR 0029) are likewise **unverified**: they are
proven only with fakes (a fake `claude`, a fake runner, an in-memory fake central service), never with a live model, a real central service or a real project.
Setup and doctor, browser acceptance (Layer 1 and Layer 2), throwaway accounts, the changelog and the docs gate (ADR 0030) are also **unverified**:
fake browser runner, fake installer, fake model; no real browser was started, nothing was downloaded, no live model ran.
**SIVA itself does not use any of this:** its unattended runs still go through `scripts/auto_run.ps1` (PowerShell), which has none of these features.

## Install
From the Flexsin marketplace (this repository):

```
claude plugin marketplace add aloks-flexsin/flexsin-claude-plugins
claude plugin install auto-runner@flexsin-claude-plugins
```

Or load it for one session without installing, from a clone: `claude --plugin-dir ./auto-runner`.
Check the manifest with `claude plugin validate ./auto-runner` (needs the `claude` CLI on PATH).

## Use
1. `/auto-runner:init` shows a preview of what the project needs and asks before writing anything (it runs `bin/auto_init.py`; `--apply` writes).
2. `/auto-runner:auto <task>` plans first. When you accept the plan it writes the request to `.auto/pending/<slug>/items.md` and starts the runner.
3. `/auto-runner:status` and `/auto-runner:log` are read-only reports (counts by state, the running item, blocked questions, recorded spend; the last iterations and log tails; `--full N` prints one whole log).

The runner can also be started by hand: `python bin/auto_run.py [--project DIR] [--max-iterations 8] [--max-budget-usd 5] [--enqueue SLUG] [--dry-run] [--allow-dirty]`.
Create `.auto/STOP` to stop a run between iterations. `.auto/lock` keeps a second run out; delete it by hand only if a run died.
Each iteration works the first *ready* item and names it in the prompt: an item whose status is `blocked` is skipped (delete or rewrite its
status line once the question is answered), and an item with a `  *Depends on:* A1, A2` line waits until each of those boxes is ticked.
When no ready item is left the run ends cleanly and lists what is blocked or waiting.

## What `init` does
Detects the test command (pytest, npm test, dotnet test, go test, cargo test) and proposes it; appends deny rules to `.claude/settings.json` (never removes or overwrites,
skips duplicates); creates `.claude/auto.json`, `.auto/queue.md` and `.gitignore` entries; reports an existing project `auto` command or scripts without touching them;
with `--with-rules` writes a short `AUTO_RULES.md` when there is no `CLAUDE.md`. Idempotent. `--remove` previews, and with `--apply` removes only what init added.

## Config: `.claude/auto.json`
Every key is optional; unknown keys and wrong types are errors that name the key.

| Key | Default | Meaning |
|---|---|---|
| `test_command` | `""` | Command that proves the suite is green. Run independently after every iteration. |
| `check_only` | `false` | `true` = the project has no tests; every status and log then says "unverified: no tests". |
| `queue_file` | `.auto/queue.md` | The one file a person reads; each item gets a status line (queued, running, done + commit, blocked + question, rejected + reasons). |
| `rules_files` | `[]` | Files the agent must read first. |
| `share_dirs` | `[]` | Folders linked into the worktree instead of copied (a venv, `node_modules`). Junction on Windows, symlink elsewhere. |
| `worktree_suffix` | `-auto` | Worktree folder = project folder name + suffix, next to the project. |
| `base_branch` | `""` | Branch the worktree starts from. Empty = detect: `origin/HEAD`, else the current branch if it is main/master/trunk/develop, else one answer is needed. |
| `branch_prefix` | `auto/` | Run branches are `<prefix><stamp>`. |
| `commit_trailer` | `""` | Line added to each commit message. |

Schema v2 sections (objects, every nested key optional, unknown nested keys and wrong types fail naming `section.key`). They are **parsed and validated only**, with one exception: `models.implementer` is passed to `claude -p` as `--model <value>` (no flag when it is unset or `inherit`). The preflight refuses any `models.<role>` value that is not `opus`, `sonnet`, `haiku`, `inherit` or a name starting with `claude-`, naming the key. A section appears in the loaded config only when the file sets it, so a config with only the keys above behaves exactly as before. `config.schema.json` describes every key.

| Section | Keys |
|---|---|
| `models` | `implementer`, `planner`, `plan_reviewer`, `work_reviewer`, `tester`, `scribe` (model alias or name) |
| `lanes` | `protected`, `sandbox` (lists of branch patterns), `deploy_command` |
| `review` | `model`, `max_plan_revisions`, `max_fix_rounds`, `max_budget_usd` |
| `budgets` | `iteration_usd`, `run_usd`, `max_iterations` |
| `docs` | `adr_dir`, `required` (list of files), `map` (doc map path, default `docs/doc-map.json`), `check` (docs gate, default on) |
| `central` | `enabled`, `url`, `project` (never a token) |
| `locks` | `path`, `stale_after_minutes` |
| `acceptance` | `browser`: `enabled`, `base_url`, `command`, `headless`, `exploratory`, `accounts`: `method` (`seed_command` or `signup`), `seed_command` |
| `changelog` | `enabled` (default off), `file` (default `CHANGELOG.md`), `versioning` (only `semver`) |

Besides `models.implementer`, three sections act: `acceptance.browser` (the browser stage below), `changelog` (the changelog step) and
`docs.map`/`docs.check` (the docs gate). Each does nothing when the file does not turn it on.

## Safety preflight
The runner refuses to start unless:
- `.claude/settings.json` denies `git push`, `git remote`, `git reset --hard`, `git clean`, checking out or switching to the base branch, and reading `.env` files;
- a `test_command` exists, or `check_only` is true;
- the repository has at least one commit;
- when `central.enabled` is true: `central.url` and `central.project` are set and the token is in the environment variable
  `AUTO_RUNNER_CENTRAL_TOKEN` (the message names what is missing; the token is never read from `.claude/auto.json`, where a
  `central.token` key is refused as unknown).

It also stops on a red suite, a STOP file, uncommitted leftovers, or two iterations without progress. Cost per iteration is recorded in `.auto/runs.jsonl` only when the CLI reports it; otherwise it is `null`.
Every run also appends to `.auto/events.jsonl` (schema `event.v1`, `bin/autoevents.py`): run start and end (with exit code and reason), each item state change (from, to) and each independent test result, one line per event, never rewritten. `runs.jsonl` keeps its old shape.

## Modules (all stdlib, all unverified beyond fakes)
| File | What it does |
|---|---|
| `bin/auto_run.py` | The runner (worktree, one ready item per iteration, independent test run, lock, STOP, events, blockers, delivery). |
| `bin/queue.py`, `bin/autocfg.py`, `bin/autosafe.py` | Queue grammar and readiness (`*Depends on:*`), config loader (v1 keys + v2 sections), safety preflight. |
| `bin/autoevents.py` | Append-only `.auto/events.jsonl`, schema `event.v1`. |
| `bin/autoblock.py` | `blocker.v1` / `decision.v1`, lifecycle state machine, local store; `list [--state S]`, `answer ID --option/--text`, `view`. |
| `bin/autodeliver.py` | `Transport` interface, `LocalTransport`, `HttpTransport` (central service), event push to central, inbox writer, resume packets; `resume ID` rebuilds a packet. |
| `bin/autostale.py` | Artifact front-matter (`version`, `hash`, `built_from`, `status`), `scan`, `status`, `revalidate`; stale artifacts refuse a resume. |
| `bin/autoeval.py` | Evaluation harness: `run --benchmark ID --runner fake`, `validate`, `validate-results FILE`. |
| `bin/auto_init.py`, `bin/auto_report.py` | Init (preview/apply/remove) and the status/log reports. |
| `bin/autosetup.py` | Capability registry and `doctor` (read-only), `plan` / `apply` / `remove` for setup. |
| `bin/autobrowser.py` | Host policy (`check_base_url`), throwaway accounts, `redact` / `scan_diff`, `browser.v1` results and the Layer 1 stage, `finding.v1` and the Layer 2 explorer. |
| `bin/automail.py` | Stdlib SMTP sink bound to 127.0.0.1; messages stored as JSON under `.auto/run/mail/`. |
| `bin/autochange.py` | Changelog sentence rules, Keep a Changelog entries, the runner's changelog commit, `release` (SemVer proposal). |
| `bin/autodocs.py` | Doc map, `check` (mapped docs changed, relative links resolve, new symbols documented) and the stale-doc scan (`gate`). |

## Setup and doctor (unverified: fake installer only)
`/auto-runner:doctor` (`python bin/autosetup.py doctor --project DIR`) is read-only and lists each capability as ok, missing or optional-admin
with a reason and the repair: browser (an installed Edge or Chrome, else Playwright Chromium), the Playwright package, Linux browser
libraries (admin only, never a failure), the mail sink, the `.gitignore` line for `.auto/run/`, `CHANGELOG.md`, and the doc map.
`/auto-runner:setup` shows one plan (a diff per file, any download with its source and a size marked "estimate"), asks once, then `apply`
runs exactly those steps, checks each with the doctor and records them in `.auto/init-manifest.json` under `setup`; `remove` takes away only
those. A download cannot be undone. Setup never reads or writes secrets and only appends deny rules. When `acceptance.browser.enabled` is true and
the doctor reports a missing capability, the preflight refuses with "run /auto-runner:setup".

## Browser acceptance (unverified: fake browser runner only, no real browser)
**Host policy.** `acceptance.browser.base_url` must be local or a test host: `localhost`, `127.0.0.1`, `[::1]`, `*.localhost`, `*.test`,
or a host with a `staging`/`dev`/`test`/`qa`/`sandbox`/`preview` label before the top-level domain. Everything else, URLs with a user name and
non-http schemes are refused; the preflight names the key.

**Accounts.** For the roles on an item's `*Browser:*` line the runner makes one throwaway account per role (an address at `auto-runner.test`, a
random password) in `.auto/run/accounts.json`, which must be git-ignored (otherwise nothing is written), and passes them only to the test
process as `AUTO_TEST_<ROLE>_EMAIL` / `AUTO_TEST_<ROLE>_PASSWORD`. With `accounts.method: seed_command` that command creates them; with
`signup` the journeys sign up through the app (the sink catches mail). Generated values are redacted from every log and event, and an item
whose commits contain one stops with `secret_leak`. No third-party service is used.

**Layer 1 decides.** An item opts in with a line such as `  *Browser:* journeys: sign-in, checkout; roles: admin, buyer`. After a green
independent test run the runner runs `acceptance.browser.command`, which must write `.auto/evidence/<item>/browser/results.json`
(`browser.v1`: per criterion id, pass/fail/skip, duration, screenshot/trace/console/network artifacts). An invalid or missing file, a failed
criterion, a non-zero exit or all-skipped is red and stops the run; a pass adds an evidence class to the item (E1 local, E3 staging-like host).
No `*Browser:*` line or no section = no stage.

**Layer 2 only advises.** With `exploratory: true`, after a passing Layer 1 a second `claude -p` explores through Playwright MCP limited to
the base URL's origin and may only write `finding.v1` files to `.auto/findings/new/`. Valid findings are stored with a proposed regression
item (never queued automatically); invalid ones go to `rejected/` with reasons. A finding never changes pass or fail.

## Changelog and release (unverified: no real agent has written a sentence)
An item may carry `  *Kind:* feature|fix|breaking|internal` (no line = internal; when `changelog.enabled` the line is required). With the
changelog on, the agent writes one user-facing sentence to `.auto/changes/<ITEM>.txt`; after a green item the runner checks it (one sentence,
10 to 200 characters, no item ids, links or generated secrets), appends it under `## [Unreleased]` (Breaking / Added / Fixed / Internal; an item
is never added twice) and makes one small changelog commit on the auto branch. A missing or invalid sentence stops with reason `changelog`.
`python bin/autochange.py release` proposes the SemVer bump and writes `.auto/release/<version>.md` with the notes and the commands a person
runs; it never tags, pushes or edits the changelog, and the deny rules then include `git tag`.

## Doc map and the docs gate
`docs/doc-map.json` (or `docs.map`) lists which docs describe which code:
`{"entries": [{"name": "...", "paths": ["glob"], "docs": ["file"], "pattern": "sql_table|route|regex", "stamp": false}]}`.
`python bin/autodocs.py check [--base REF] [--head REF]` fails when a mapped file changed without its docs, a changed Markdown file has a dead
relative link, or a newly added symbol (from `pattern`) is in none of the docs. For `stamp: true` entries a changed watched file makes the
doc stale until `python bin/autostale.py revalidate DOC --from a.py,b.py` is committed in the same range. After a green item, before the
changelog step, the runner runs this gate on the item's commits; a failure stops with reason `docs`. No map or `docs.check: false` = no gate.
This repository's own `docs/doc-map.json` is the working example: it maps the plugin's code to this README.

## Blocker file contract (unverified with a real agent)
Instead of guessing, the agent writes one JSON object per question to `.auto/blockers/new/<name>.json` with the `blocker.v1` content fields:
`question`, `why_needed`, `prevents`, `impact_if_delayed`, `impact_if_wrong`, `owner_role`, `class` (B1-B5), `options` (`[{label, tradeoffs}]`),
`recommendation`, and optionally `evidence`, `assumptions`, `downstream`, `due`, `blocking_artifact`, `parent_blocker`. The runner owns id, project,
task, run, state, history and timestamps. A valid file is stored OPEN in `.auto/blockers/<id>.json`, the item becomes `blocked (<id>)`, a blocker
event is written and the run moves to the next ready item. An invalid file goes to `.auto/blockers/rejected/` with a `.reasons.txt`; the item is
blocked with that message. `.auto/QUESTIONS.md` is a generated view; never edit it. Answer with
`python bin/autoblock.py answer ID --option LABEL [--text TEXT]`: the decision is saved as data (never run as a command) and waits for delivery.
Only RESUMED and CLOSED count as unblocked; "answered" is not "resumed". With no blocker file present the runner behaves as before.

## Delivery contract
Pull-based: the runner pulls answered decisions at start and between iterations, writes `.auto/inbox/<blocker>.v<N>.json` once per decision
version, and acknowledges only when it selects the item again; the next prompt then carries `.auto/resume/<id>.md` (blocker, decision, artifact
hashes, last evidence, failed attempts, ledger excerpt). Outages change nothing and are retried. The request/response shapes and state rules are in
`docs/delivery-contract.md`. Three implementations pass the same contract tests: `LocalTransport`, the in-memory fake in
`tests/auto_runner/fake_central.py`, and `HttpTransport(base_url, token, send=None, timeout=10)`, which talks to the central service
(`central/`) with stdlib `urllib`: no reply, a timeout, a 5xx or an unreadable reply raise `TransportUnavailable`; on ack 404 is `KeyError` and 409
`ValueError`; any other 4xx (bad token, wrong scope) raises `TransportRefused`. The token goes only in the `Authorization` header. The tests drive
it through the real app's Flask test client; it has not run over a real network.

With `"central": {"enabled": true, "url": "...", "project": "<slug>"}` in `.claude/auto.json` and `AUTO_RUNNER_CENTRAL_TOKEN` set,
the runner pulls and acknowledges answers through `HttpTransport` instead of `LocalTransport`, and events, blockers and pulls carry
`central.project` (which must equal the token's project) instead of the folder name. The token is removed from the environment of the
agent and the test command, added to the values redacted from events, and never written to a log, an event or a file. Without the
section, or with `enabled` false, nothing changes: answers stay local. This is proven with a fake `send` only (no real service).

With central on, the runner also sends its ledger there: before each pull and once more after `run_end`, `autodeliver.push()` posts the
lines of `.auto/events.jsonl` it has not sent yet (only events of `central.project`, up to 100 a request), and records how far it got in
`.auto/central-push.json`. A `raised` blocker event carries the blocker's content (`autoblock.content()`: question, options, recommendation,
impacts, assumptions, evidence), so a person can answer it on the service's blocker pages. An outage or a refused token stops the push with a
message and the next push starts again from the same line (the service drops duplicates by event id); an event the service will never take
(400, 409, 413) is skipped and its id kept in the cursor file. A push never stops a run. While central is on, answer blockers on the service:
`autoblock.py answer` saves a local decision the runner no longer reads. Proven through the real app's Flask test client (no socket), not over
a real network.

## Evaluation harness (no real-model run yet)
`eval/` holds a rubric, a smoke benchmark and seven benchmark briefs with held-out test descriptions kept out of the agent's view. A criterion passes
only on executable held-out checks; the runner's own approval never counts; a score with nothing to measure is null. The fake runner is evidence class
E0: it proves the wiring, not delivery quality. Real baselines are manual (`eval/BASELINE.md`, results format `baseline.v1`), record model versions
and variance, and no target scores are set before a baseline exists. The briefs have no executable checks yet, and two need a seed codebase the
harness cannot copy.

## What the plugin cannot enforce
- The deny rules are Claude Code permission settings. The plugin only checks they are written; it cannot stop a person or another tool from changing them, or a command written in a form the rules do not match.
- It does not sandbox the machine: the agent runs with your user account's file access inside the worktree and anything your permission mode allows.
- A green suite shows the tests pass, not that the work is right. Read the diff (`git log <base>..<branch>`) before merging. Merging and pushing are always yours.
- Cost and time limits are per run flags, enforced by the `claude` CLI, not by the plugin.

## Uninstall
`claude plugin uninstall auto-runner`, then `claude plugin marketplace remove flexsin-claude-plugins`. In a project: `python bin/auto_init.py --remove` previews what init added; add `--apply` to remove it.
Remove a finished worktree with `git worktree remove <folder>` (unlink any shared folder junction first so the real one is never touched).
