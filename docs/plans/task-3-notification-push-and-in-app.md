# Plan: in-app and browser push notifications for what waits on a person (central task #3)

## Intent
When something on the central service waits for a person, that person is told without having to open My work: a bell
in the top bar with a count and a Notifications page (in-app), and, if they turned it on in their browser, a browser
push notification that opens the right page. Every notification and every push attempt is logged, so a person can
check what was sent, to whom and whether it arrived. Success: a manager sees the bell count rise and gets a push when
a plan waits for them; clicking it opens the plan; the log shows "push: sent".
Out of scope: email or chat notifications, per-kind notification settings (everyone gets the kinds below), and iOS
push on Safari without adding the site to the home screen (a browser rule).

"Pending task" is read as **anything that waits on that person**:
- a plan waiting for approval: the task's assignee and the project's managers, owners (and admins who are members);
- a blocker waiting for an answer: the project's managers and owners;
- a task assigned to you by someone else (an admin adding it for you);
- your plan sent back with a comment ("changes requested").

## Context (what exists to build on)
- Where these moments happen: `tasks.post_plan` and `tasks.api_record` (plan waits), `tasks.decide` (sent back /
  decided), `tasks.task_add` (assigned by someone else), `ingest.store` → `_upsert_blocker` (blocker raised),
  `deliveries.record_decision` (blocker answered).
- `shell.nav_badge` already counts decisions for the top bar; `mywork.collect` lists them; `audit.record` logs actions.
- `db.init_db` creates new tables from `schema.sql` (`CREATE TABLE IF NOT EXISTS`); a person runs `init-db`.
- CSP: `default-src 'self'; script-src 'self'`: a service worker served from this site is allowed; no inline script.
- Config comes from environment variables (`config.py`); tests use fakes only.

## Key decisions
- **One `notification` row per person and event** (kind, project, text, link, created, read_at, resolved_at, push
  state). It is both the in-app list and the log. A `dedupe` key (`plan:<project>:<N>`, `blocker:<project>:<id>`)
  stops repeats.
- **Notifications resolve themselves.** When the plan is decided or the blocker answered, its open notifications are
  marked resolved for everyone, so the bell counts only what still waits.
- **Browser push with the standard Web Push protocol and VAPID keys**, sent with `pywebpush`. Keys come from
  `CENTRAL_VAPID_PUBLIC_KEY`, `CENTRAL_VAPID_PRIVATE_KEY`, `CENTRAL_VAPID_SUBJECT`. Without them push is off and the page says so;
  in-app still works.
- **Sending happens in the request that caused it**, with a short timeout per subscription (5 s), behind a
  `push_sender` seam (a fake in tests). A subscription the push service says is gone (404/410) is deleted. No queue or
  worker: the service runs on serverless functions; a few recipients per event is fine.
- **Turning push on is per browser**, from the Account page ("Notifications on this browser: Turn on"), through
  `static/app.js` and a service worker at `/sw.js`. Works only after the person allows it in the browser.

## Slices
1. **In-app notifications.** A bell with a count in the top bar and a Notifications page for plans, blockers,
   assignments and sent-back plans; reading and resolving clear them.
2. **Browser push.** A person turns on notifications for their browser on the Account page and gets a push for each
   new notification; clicking it opens the page.
3. **Notification log.** Each notification shows its delivery (in-app, push sent / failed / off), and admins have a
   log page across everyone.

## Journeys
- **J1 A waiting plan rings the bell.** Slice 1. Roles: developer, manager.
  1. As developer, let a runner post a plan for your task (or post one through the API).
  2. As manager, open any page.
  *Expect:* the bell shows 1; the Notifications page lists "Plan waiting: #N <title>"; opening it shows the plan.
- **J2 Deciding clears it.** Slice 1. Roles: manager.
  1. As manager, approve that plan.
  *Expect:* the bell count drops to 0 and the notification shows as done.
- **J3 A push arrives.** Slice 2. Roles: manager.
  1. As manager, on the Account page press "Turn on" and allow notifications in the browser.
  2. Have a runner raise a blocker in a project you manage.
  *Expect:* a browser notification "Blocker waiting: <question>"; clicking it opens the blocker's page.
- **J4 The log shows what was sent.** Slice 3. Roles: admin.
  1. As admin, open Admin › Notifications.
  *Expect:* the J3 notification is listed with "push sent" and the recipient.

## Reuse check
| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Sending Web Push | `pywebpush` https://pypi.org/project/pywebpush/ (2.5.0 on PyPI) | free | MPL-2.0 (unverified) | integrate | the standard Python library for VAPID Web Push; writing the encryption ourselves is risky |
| Push delivery | browsers' own push services (FCM, Mozilla, Apple) via Web Push | free | n/a | integrate | no third-party account; only VAPID keys |
| Hosted alternatives | OneSignal, Firebase Cloud Messaging SDK | free tiers with limits | proprietary SDKs | not chosen | another account, another script origin (would need CSP changes), and data leaves the service |
| In-app list and log | our own table and pages | free | n/a | build | small; fits the existing pages and audit |

## End to end
- Data: two new tables, `notification` and `push_subscription`, created by `python -m central init-db`.
- Security: notifications only for people who may act in that project (same role checks as the pages); push payload
  holds only the short text and a same-site link; subscriptions belong to one account and are deleted with it; the
  VAPID private key comes from an environment variable and is never shown; CSRF on every page form and on the
  subscribe call.
- Errors: a push that fails is logged on the notification and never fails the request that caused it.
- Configuration and deployment: three environment variables for push (a person generates the keys and sets them).
- Rollback: unset the VAPID variables to stop push; in-app can stay; the tables are additive.
- Docs: `auto-runner/central/README.md`, the guide's My work section, the root `CHANGELOG.md`, all marked unverified
  until a person tests on the live service.

## Queue items
- [ ] **N1 Notifications are created when something waits on a person.** Add a `notification` table to `schema.sql` (id, account_id, project_id, kind, text, link, dedupe, created_at, read_at, resolved_at, push_state, push_detail) and `central/notifications.py` with `notify(db, account_ids, project, kind, text, link, dedupe, now)` (skips duplicates), `resolve(db, dedupe, now)`, `unread_count`, `for_account`. Call it from `tasks.post_plan` / `tasks.api_record` with page approval (assignee and managers+), `tasks.decide` with changes (assignee) and on any decision (resolve), `tasks.task_add` when an admin assigns to someone else and `tasks.release` when it gives the task to a new assignee (assignee), `ingest.store` for a new raised blocker (managers and owners of the project), `deliveries.record_decision` (resolve); `tasks.release` also resolves the task's `plan` and `changes` notifications. Recipients are project members only: a site admin who is not a member of the project gets none (they still see everything on My work). Texts and links: `plan` "Plan waiting: #N <title>" → the task page; `changes` "Changes asked on #N <title>" → the task page; `assigned` "Assigned to you: #N <title>" → the task page; `blocker` "Blocker waiting: <question>" → `/projects/<slug>/blockers/<id>`. Update `auto-runner/central/README.md` for what this item adds.
  *Acceptance:* `tests/central/test_notifications.py`: each of the four kinds reaches exactly the right people (and not viewers or strangers) with the text and link above, a reassignment through `release` notifies the new assignee and resolves the old plan/changes notifications, a site admin who is not a member gets nothing, a repeated event makes no duplicate, deciding or answering resolves them, `init-db` adds the table to an existing database.
  *Slice:* 1 In-app notifications
  *Kind:* feature
- [ ] **N2 Bell and Notifications page.** Top bar bell with the unread, unresolved count (from `shell`'s context processor); `GET /notifications` lists the person's notifications, newest first, waiting ones on top, each with its project, time and link; `POST /notifications/<id>/open` marks it read and redirects to its link (same-site only, `ui.safe_next`); `POST /notifications/read-all`. Resolved notifications stay listed below, marked "Done". No inline styles or scripts. Update `auto-runner/central/README.md` for what this item adds.
  *Acceptance:* `tests/central/test_notification_pages.py`: the bell shows the count and disappears at zero, a resolved notification is shown as "Done" and not counted, the page lists only the person's own notifications, opening marks read and redirects, another person's id gets 404, read-all clears the count, forms carry CSRF, `test_pages_style.py` passes.
  *Slice:* 1 In-app notifications
  *Kind:* feature
- [ ] **N3 Push subscriptions and settings.** Add `push_subscription` table (account_id, endpoint unique, p256dh, auth, user_agent, created_at, last_ok_at, failures); `config.py` reads `CENTRAL_VAPID_PUBLIC_KEY`, `CENTRAL_VAPID_PRIVATE_KEY`, `CENTRAL_VAPID_SUBJECT`; `POST /push/subscribe` and `POST /push/unsubscribe` (signed in, JSON with the page's CSRF token in an `X-CSRF-Token` header, endpoint must be https); Account page section "Notifications on this browser" (Turn on / Turn off; "push is not set up on this service" without keys). Make `auth._check_csrf` also accept the `X-CSRF-Token` header when the form field is missing. Add `pywebpush` to `central/requirements.txt`. Update `auto-runner/central/README.md` for what this item adds.
  *Acceptance:* `tests/central/test_push_subscriptions.py`: a signed-in JSON POST with the right `X-CSRF-Token` header passes and a wrong one gets 400, subscribe stores one row per endpoint, a second subscribe updates it, unsubscribe deletes only your own, missing CSRF gets 400, a non-https endpoint gets 400, the Account page shows the right state with and without keys, `init-db` adds the table.
  *Slice:* 2 Browser push
  *Kind:* feature
- [ ] **N4 Service worker and the browser side.** Serve `static/sw.js` at `/sw.js` (scope `/`): on `push` show the notification (title, body, link from the payload); on `notificationclick` open or focus the link. In `static/app.js`: the Turn on button asks permission, subscribes with the VAPID public key from a `data-` attribute and posts the subscription; Turn off unsubscribes. Same-origin only; works under the CSP. Update `auto-runner/central/README.md` for what this item adds.
  *Acceptance:* `tests/central/test_push_subscriptions.py` checks `/sw.js` is served with `Content-Type: text/javascript` and `Service-Worker-Allowed: /`, and that `sw.js` and `app.js` contain the push, notificationclick and subscribe handlers; `test_pages_style.py` passes.
  *Slice:* 2 Browser push
  *Kind:* feature
- [ ] **N5 Sending pushes.** `notifications.notify` sends a Web Push for each new notification to the recipient's subscriptions through `push_sender(subscription, payload, vapid)` (pywebpush in production, a fake in tests), with a 5-second timeout; records `push_state` (`off` without keys or subscriptions, `sent`, `failed` with the reason in `push_detail`); deletes a subscription on 404/410; never raises into the caller. Update `auto-runner/central/README.md` for what this item adds.
  *Acceptance:* tests in `tests/central/test_notifications.py` with a fake sender: a subscribed person gets one push per notification with the text and link, no keys means `off`, a 410 deletes the subscription, a sender error marks `failed` and the request still succeeds.
  *Slice:* 2 Browser push
  *Kind:* feature
- [ ] **N6 Notification log and docs.** Show each notification's delivery on `/notifications` ("in-app", "push sent", "push failed: <reason>", "push off"); add `GET /admin/notifications` (admins only) listing recent notifications across people with recipient, project, kind, delivery and time, filterable by `?account=` and `?kind=`. Update `auto-runner/central/README.md` (the log page), `auto-runner/docs/central-guide.md` (a "Notifications" section) and the root `CHANGELOG.md`, marked unverified until tested live.
  *Acceptance:* `tests/central/test_notification_pages.py`: delivery words shown per state, the admin log lists everyone's and filters work, non-admins get 404; `python -m pytest -q` passes (doc-map check included).
  *Slice:* 3 Notification log
  *Kind:* feature

## Spec check
First run: 4 findings, all fixed. N3 now lets `auth._check_csrf` read an `X-CSRF-Token` header (the subscribe call is
JSON). N1 names each kind's text and link, and also notifies a new assignee given the task by an admin's release. N2
shows resolved notifications as "Done". Second run: 4 findings, all fixed. Every item N1–N5 now updates `auto-runner/central/README.md`
(the doc map requires it). `release` resolves the plan/changes notifications. Recipients are project members only
(non-member site admins are not notified). J4 uses the same "push sent" wording as N6. The two check rounds allowed are used up.

## Needs a person
- **Push keys and deployment:** generate a VAPID key pair (for example `npx web-push generate-vapid-keys`, or
  `vapid --gen` from `py-vapid`), set `CENTRAL_VAPID_PUBLIC_KEY`, `CENTRAL_VAPID_PRIVATE_KEY` and `CENTRAL_VAPID_SUBJECT` (`mailto:` your
  address) on the hosting, then deploy. Run `python -m central init-db` (two new tables).
- **Who is notified (what the items assume):** plans to the assignee and the project's managers/owners; blockers to
  managers/owners; assignments and sent-back plans to the assignee; project members only, so site admins who are not
  members get none. Ask for changes if admins should be notified everywhere.
- **Manual test:** J1–J4 on the live service in Chrome or Firefox on a computer, and on Android Chrome. iPhone push
  works only when the site is added to the home screen; that is not part of this task.
