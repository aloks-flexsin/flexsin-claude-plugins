# Plan: the auto-runner's pull requests follow the team's PR description rules (task #25)

## Intent
Every pull request the auto-runner opens must use the description layout in task #25: a header (PR ID, author,
branch, linked issue, status), then the ten sections Summary, Problem Statement, Proposed Changes, Technical
Implementation, Testing & Validation, Impact Assessment, Screenshots / Evidence, Known Limitations / Pending Work,
Reviewer Checklist, Deployment Notes, and an "AI Agent Self-Assessment". Today `auto_run.publish` writes one sentence
and the commit list. Success: a PR opened by a run (and one reused and marked ready) carries that layout, filled from
facts the run already has; nothing in it is invented.
Out of scope: pull requests people open by hand; the central service; changing how or when the runner pushes.

## Context (what exists to build on)
- `auto_run.publish(project, wt, cfg, base, branch, ...)` builds `body` and calls `gh pr create` (or the REST
  fallback `rest_pull_request`) with it; a draft PR is opened when the run stopped with items open.
- The run knows: the central task (`autotasks.current`: number, title; `items` from task #30), the queue items with
  their acceptance lines and ticks (`queue.parse_items`), the commits (`git log start..branch`), the changed files
  (`git diff --name-status start...branch`), the independent test runs (`tests` events in the ledger, and the summary
  line "suite at the last independent run"), the changelog sentences (`.auto/changes/<item>.txt`), blocked or open
  items and their status lines, and screenshots from browser acceptance (`autobrowser` evidence paths).

## Key decisions
- **One builder, `bin/autopr.py`:** `pr_body(facts) -> str` (pure, tested) and `gather(project, wt, cfg, base, branch,
  start) -> facts` (reads git, the queue, the ledger and the change files). `publish` uses it for every PR it opens.
- **Facts only.** A section with nothing known says so ("None recorded by this run.") instead of guessing. Checklist
  boxes are ticked only from evidence: "Unit tests passed" from a green independent test run, "Build successful"
  only when a check command ran green; everything else stays unticked for the reviewer.
- **Self-assessment from evidence:** implementation confidence High when every item of the task is done, Medium when
  some are open, Low when an item is blocked; test confidence from the last independent run (green High, none Low,
  red Low); recommended action "Approve" only when both are High, else "Needs Human Review".
- **Deployment notes** name migrations it can see (a changed `schema.sql`, `db.py` `COLUMNS_ADDED`, a `migrations/`
  path) and config changes (`.claude/auto.json`, `config.schema.json`, `.env.example`); otherwise "None detected".

## The template (task #25, word for word; Z1 saves it as `auto-runner/docs/pr-template.md`)
```
# Pull Request: [Short, Descriptive Title]

**PR ID:** [Auto-generated]
**Author:** [Developer / AI Agent]
**Branch:** `feature/branch-name` → `main`
**Linked Issue:** [JIRA / GitHub / Bitbucket ticket]
**Status:** Draft / Ready for Review

---

## 1. Summary
## 2. Problem Statement
## 3. Proposed Changes
## 4. Technical Implementation
- Files/modules modified:
- Architecture or logic changes:
- APIs/database changes:
- Dependencies added/updated:
## 5. Testing & Validation
- [ ] Unit tests passed
- [ ] Integration tests passed
- [ ] Existing regression tests passed
- [ ] Build successful
- [ ] Linting/type checks passed
- [ ] Edge cases validated
**Test Results:** [Summary / CI links]
## 6. Impact Assessment
- Potential breaking changes:
- Performance implications:
- Security implications:
- Backward compatibility:
- Affected modules/services:
## 7. Screenshots / Evidence
## 8. Known Limitations / Pending Work
## 9. Reviewer Checklist
- [ ] Requirements satisfied
- [ ] Code quality and maintainability verified
- [ ] No unnecessary code or dependencies
- [ ] Security considerations reviewed
- [ ] Tests provide sufficient coverage
- [ ] No unrelated changes included
## 10. Deployment Notes
- Environment/configuration changes:
- Migration requirements:
- Deployment considerations:
- Rollback strategy:

---

**AI Agent Self-Assessment**

- Implementation confidence: [High / Medium / Low]
- Test confidence: [High / Medium / Low]
- Unresolved concerns: [Details]
- Recommended action: [Approve / Request Changes / Needs Human Review]
```

**How each part is filled** (facts only; "None recorded by this run." where nothing is known):
- Title: the plan's first `# ` heading, else the branch's brief. PR ID: "assigned by GitHub on creation". Author:
  `<user>/<account>-claude-auto-runner (AI agent)`. Branch: the run branch → base. Linked issue: "central task #N"
  or "none". Status: "Draft" for a draft PR, else "Ready for Review".
- 1 Summary: the task title and the number of items done of the run's items. 2 Problem Statement: the "## Intent" section
  of the request's plan (`docs/plans/<request>.md`, first 1500 characters), else "None recorded by this run.". 3 Proposed Changes: one line per item
  (ID, title, and its change sentence when there is one). 4: files from `git diff --name-status`; architecture:
  "See the commits:" with the commit list; APIs/database: the migration paths it sees; dependencies: changed
  manifest files (`package.json`, `pyproject.toml`, `requirements*.txt`, lock files) or "None detected".
- 5 ticks: "Unit tests passed" and "Existing regression tests passed" when the last independent run of the full
  test command was green; "Build successful" when the check command ran green; the others are never ticked.
  Test Results: the "suite at the last independent run" line, else "No independent test run recorded.".
- 6: breaking changes from items of kind `breaking`, else "None declared"; affected modules: the top-level folders
  of the changed files; the other lines "Not assessed by the runner: reviewer to judge.".
- 7: browser evidence paths, else "None recorded by this run.". 8: open, blocked or set-aside items with their
  status lines. 9: all unticked (for the reviewer). 10: config changes seen (`.claude/auto.json`,
  `config.schema.json`, `.env.example`), migrations seen, "None detected" otherwise; rollback strategy: "Revert this
  pull request's merge commit.".
- Self-assessment, rules in order of precedence (the first that applies wins). Implementation confidence: any item
  of the run blocked -> **Low**; else any item open -> **Medium**; else **High**. Test confidence: the last
  independent run of the full test command green -> **High**; else no full run recorded but every item-level run
  (`item_test_command`) green -> **Medium**; else (a red run of either kind, or no run at all) -> **Low**. Unresolved concerns: the open and blocked items, else "None". Recommended
  action: "Approve" only when both confidences are High, else "Needs Human Review".

## Slices
1. **PR descriptions follow the team template.**

## Journeys
- **J1 A run opens a pull request.** Slice 1. Roles: developer.
  1. As developer, let the runner finish a task's items and publish.
  *Expect:* the pull request description has the header and the ten sections in order, filled from the run.

## Reuse check
| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Item facts | `queue.parse_items`, `autochange.item_facts` | free | n/a | reuse | same parsers as the runner |
| Test results | the run ledger's `tests` events | free | n/a | reuse | already recorded per run |

## End to end
- Plugin only; no service change. Docs: `auto-runner/README.md` (publishing section), root `CHANGELOG.md`.

## Queue items
- [ ] **Z1 A builder that fills the team's PR description template from the run's facts.** Add `auto-runner/docs/pr-template.md` holding the template in this request's plan, `docs/plans/task-25-pr-description-rules-fro-autor.md` (the runner copies the approved plan there when it queues this request: `auto_run.apply_pending` writes `docs/plans/<request>.md` in the same commit as the queue items), section "The template", word for word. Add `auto-runner/bin/autopr.py` with `gather(project, wt, cfg, base, branch, start)` (the task number from `autotasks.current` when there is one; the request's plan: the `docs/plans/*.md` file added on the branch in `start...branch` (`git diff --name-only --diff-filter=A start...branch -- docs/plans/`), which `auto_run.apply_pending` commits with the queue items for every request, with or without a central task; the title from that plan's first `# ` heading, else the branch's brief; the problem statement from its "## Intent" section, and "None recorded by this run." when there is no such file or section; the run's items with tick, kind, acceptance line and status line from `queue.parse_items`/`queue.statuses`; commits from `git log start..branch`; changed files from `git diff --name-status start...branch`; the last independent test runs from the ledger's `tests` events, full and item-level; change sentences from `.auto/changes/<item>.txt`; browser evidence paths). Update `auto-runner/README.md` and the root `CHANGELOG.md`.
  *Acceptance:* `auto-runner/docs/pr-template.md` exists and holds the plan's template word for word; new tests in `tests/auto_runner/test_autopr.py`: `gather` on a scratch git repo with a fake ledger, a queue with ticked, open and blocked items, a change sentence and a plan file under `docs/plans/<request>.md` returns the commits, the changed files, the items with their states, the full and item-level test results, the sentence, the title from the plan's `# ` heading and the Intent text, and the evidence path of a fake ledger `browser` event; with no plan file on the branch the title is the branch's brief and the problem statement "None recorded by this run."; the affected test files pass.
  *Slice:* 1 PR descriptions follow the team template
  *Kind:* feature
- [ ] **Z2 The PR description is written from those facts.** In `auto-runner/bin/autopr.py` add a pure `pr_body(facts, draft)` that writes the template's header and these headings in this order: `## 1. Summary`, `## 2. Problem Statement`, `## 3. Proposed Changes`, `## 4. Technical Implementation`, `## 5. Testing & Validation`, `## 6. Impact Assessment`, `## 7. Screenshots / Evidence`, `## 8. Known Limitations / Pending Work`, `## 9. Reviewer Checklist`, `## 10. Deployment Notes`, `**AI Agent Self-Assessment**`, with the box labels of sections 5 and 9 and the four self-assessment fields exactly as in the template, filled as section "How each part is filled" of `docs/plans/task-25-pr-description-rules-fro-autor.md` says (the source of every section, the header fields with the status wording "Draft" or "Ready for Review", the evidence that ticks each box) (facts only; "None recorded by this run." where nothing is known; boxes ticked only from evidence; the confidence rules in their order of precedence). Update `auto-runner/README.md` and the root `CHANGELOG.md`.
  *Acceptance:* new tests in `tests/auto_runner/test_autopr.py` on synthetic facts: the header and every heading above appear once and in order; every box label and self-assessment field of `auto-runner/docs/pr-template.md` appears in the output; empty facts give "None recorded by this run." and no ticked box; a green full run ticks "Unit tests passed" and "Existing regression tests passed" and gives test confidence High; only green item-level runs give Medium; a red run or none gives Low; all items ticked gives implementation confidence High, an open item Medium, a blocked item Low (also when another is open); Approve only when both are High, else Needs Human Review; a changed `schema.sql` is listed under 10 as a migration and a changed `package.json` under dependencies; the affected test files pass.
  *Depends on:* Z1
  *Slice:* 1 PR descriptions follow the team template
  *Kind:* feature
- [ ] **Z3 Every pull request the runner opens uses that description.** In `auto-runner/bin/auto_run.py` `publish`, build the body with `autopr.pr_body(autopr.gather(...), draft)` instead of today's sentence and commit list, for `gh pr create`, for the REST fallback (`rest_pull_request`), and for every pull request that already exists, draft or not: run `gh pr edit <url> --body <body>` (after `gh pr ready` only when the run is not a draft, as today); a failed edit is reported in `pr_error` and never stops the run. Update `auto-runner/README.md` (the publishing section) and the root `CHANGELOG.md`.
  *Acceptance:* tests in `tests/auto_runner/` with the existing fake `gh` of the publish tests: the body passed to `gh pr create` starts with the template's header and holds the ten headings; the REST fallback sends the same body; a reused pull request gets `gh pr edit --body` with it, also when the run is a draft (then without `gh pr ready`), and a failing edit is reported without failing the run; a draft run's body says Status: Draft; the affected test files pass.
  *Depends on:* Z2
  *Slice:* 1 PR descriptions follow the team template
  *Kind:* feature

## Needs a person
None.

## Spec check
Eight rounds; findings fixed (the template and fill rules named, confidence rules by precedence, the plan found
from the branch, the work split into Z1 gather, Z2 pr_body and Z3 publish). Last run: no findings.
