# Plan: ask once, on any page, to turn on push notifications

## Intent
Today push is only offered deep in the Account page, so most people never turn it on and miss plans and blockers that
wait for them. Each signed-in person should be asked **once per browser**, on whatever page they land, whether to get
notifications here. Success: on a browser that has never decided, a small bar appears under the top bar ("Get a
notification when a plan or blocker waits for you." **Turn on** · **Not now**); Turn on shows the browser's own
permission prompt and subscribes; either answer hides the bar for good on that browser.
Out of scope: e-mail notifications, choosing which kinds to receive, and changing what triggers a notification.

## Context (what exists to build on)
- `static/app.js` "Push on this browser" block: registers `/sw.js`, `Notification.requestPermission()`, subscribes with
  the VAPID public key and posts to `push.subscribe_post` with `X-CSRF-Token` (Account page `[data-push]` box).
- `push.account_state(db, account)` (`configured`, `public_key`, `count`); `push.configured()`.
- `templates/base.html` top bar (bell); CSP: no inline script or style, all behaviour in `app.js`.

## Key decisions
1. **A bar with a button, not the browser prompt on page load.** Browsers require a click before asking for
   notification permission (Chrome and Firefox silently block or hide prompts shown on load, and Safari refuses them),
   and a cold prompt is usually answered "Block", which can only be undone in browser settings. So our bar asks first;
   the browser's prompt follows only on **Turn on**.
2. **Shown only when it can work:** push configured on the service, the browser supports push, permission is still
   `default` (never decided), this browser has no subscription, and the person has not answered the bar here. If
   permission is already `granted` but there is no subscription, the bar still shows (Turn on subscribes silently).
3. **"Not now" is remembered per browser** in `localStorage` (`push-ask` = `dismissed`), because push itself is per
   browser; wrapped in try/catch so private windows just show the bar again. The Account page keeps Turn on / Turn off
   for changing your mind.
4. **One code path:** the subscribe steps move into a shared `enablePush(box)` function used by both the bar and the
   Account page, so they cannot drift apart.

## Slices
1. **Ask once on any page.**

## Journeys
- **J1 First visit.** Slice 1. Roles: developer.
  1. As developer, in a browser that never decided, open any page. *Expect:* the bar appears under the top bar.
  2. Click Turn on and allow. *Expect:* the bar disappears; Account says "Turned on in this browser"; the next plan
     waiting for you arrives as a push.
- **J2 Not now.** Click Not now. *Expect:* the bar disappears and does not come back on reload in this browser.

## Reuse check
| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Subscribe flow | `app.js` push block | free | n/a | reuse (extract) | one path for bar and Account page |

## End to end
- No new tables, routes or settings. Docs: `auto-runner/central/README.md` (push section), root `CHANGELOG.md`.

## Queue items
- [ ] **A1 Ask once per browser to turn on push.** In `auto-runner/central/templates/base.html`, for a signed-in account when `push.configured()`, render a hidden bar `<div class="push-ask" data-push-ask data-vapid-public-key data-csrf-token data-subscribe-url hidden>` under the top bar with the text "Get a notification when a plan or blocker waits for you." and buttons **Turn on** (`data-push-ask-on`) and **Not now** (`data-push-ask-off`); not rendered on the Account page (it has its own section). In `static/app.js` extract the Account page's subscribe steps into `enablePush(box)` (permission → register `/sw.js` → subscribe → post) used by both; show the bar only when push is supported, `Notification.permission !== "denied"`, no subscription exists and `localStorage["push-ask"]` is not set (reads and writes in try/catch); Turn on → `enablePush`, then hide and store `done`; on refusal show the same blocked message in the bar; Not now → hide and store `dismissed`. Styles in `static/style.css` with existing tokens, working at 390 px. Update `auto-runner/central/README.md` and `CHANGELOG.md`.
  *Acceptance:* `tests/central/test_push_ask.py`: the bar's markup (with key, CSRF token and subscribe URL) is on a signed-in page when the three VAPID settings are set; absent without them, when signed out, and on the Account page; the template scan finds no inline script or style; `python -m pytest -q` passes. Browser behaviour checked once with Playwright (bar visible on a fresh profile, hidden after Not now and a reload).
  *Slice:* 1 Ask once on any page
  *Kind:* feature

## Needs a person
- Nothing to deploy beyond the usual redeploy. Check J1 on your phone and laptop after it ships.
