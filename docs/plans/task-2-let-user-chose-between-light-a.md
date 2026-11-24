# Plan: light mode by default, with a light/dark choice (central task #2)

## Intent
People using the central service get **light mode by default**, whatever their system setting, and can switch to dark
mode (and back) with one click. The choice sticks on that browser across pages and visits. Success shows on any page:
it opens light, the top bar has "Dark mode", pressing it turns the page dark and keeps it dark after reload and
sign-out. Out of scope: following the system setting automatically (the request asks for light as the default),
per-page themes, and a theme stored on the account across devices (see Key decisions).

## Context (what exists to build on)
- `static/style.css` defines colour tokens on `:root` and switches them with two `@media (prefers-color-scheme:dark)`
  blocks (the base tokens, line 7; the status tones, line 101). Everything else uses the tokens, so one switch point
  changes the whole site.
- `templates/base.html` renders `<html lang="en">` and the top bar (signed in: nav and account menu; signed out: brand).
- CSP is `style-src 'self'; script-src 'self'`: no inline style or script (`tests/central/test_pages_style.py`).
- Every page POST carries `csrf_token` (`auth._check_csrf`); `ui.safe_next` returns only same-site paths.

## Key decisions
- **Where the choice is kept: a cookie** (`theme=light|dark`, one year, `Secure`, `SameSite=Lax`, not HttpOnly-sensitive
  data). The server reads it and renders `<html data-theme="...">`, so the right colours arrive with the first byte:
  no flash, no JavaScript, no migration, and it works on the sign-in page too. Storing it on the account would follow a
  person across devices but needs a migration and does nothing before sign-in; not worth it now.
- **How it switches: a small form** in the top bar (signed in and signed out) posting to `POST /theme` with the other
  value and the current path as `next` (checked with `ui.safe_next`). Works with JavaScript off; CSRF like every form.
- **Dark styles keyed on the attribute**, not the media query: `:root[data-theme="dark"]` replaces both
  `@media (prefers-color-scheme:dark)` blocks, with the same token values, so dark mode looks exactly as today.
- No "System" option: the request asks for light by default and a toggle.

## Slices
1. **Light by default.** Every page renders light regardless of the system setting.
2. **Choose dark mode.** A "Dark mode" / "Light mode" button in the top bar switches the theme and the choice sticks.

## Journeys
- **J1 Light by default.** Slice 1. Roles: viewer.
  1. As viewer, with the computer set to dark mode, open My work.
  *Expect:* the page is light (white cards on a light grey background).
- **J2 Switch to dark and keep it.** Slice 2. Roles: viewer.
  1. As viewer, press "Dark mode" in the top bar.
  2. Reload the page, then open a project.
  *Expect:* both pages are dark and the button now says "Light mode"; pressing it makes them light again.

## Reuse check
| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Theme switch | CSS custom properties already on `:root`; attribute selector | free | n/a | reuse | the tokens exist; only the switch point moves |
| Remembering the choice | a cookie set by the server | free | n/a | build (small) | no flash, no JS, no migration; localStorage would need JS and flash before it runs |

## End to end
- Data: none stored on the service; one cookie on the browser.
- Security: the cookie only selects `light` or `dark` (anything else is ignored); `next` is checked with
  `ui.safe_next` (no open redirect); the form carries CSRF like every other.
- Errors: an unknown cookie value renders light.
- Rollback: revert the template and CSS; old cookies are ignored.
- Docs: `auto-runner/central/README.md` and the root `CHANGELOG.md`.

## Queue items
- [ ] **L1 Light by default.** In `static/style.css`, replace both `@media (prefers-color-scheme:dark)` blocks with `:root[data-theme="dark"]` rules holding the same token values; in `templates/base.html` render `<html lang="en" data-theme="{{ theme }}">` with `theme` from a context processor (`ui.theme()`: the `theme` cookie when it is `dark`, else `light`). No other page changes.
  *Acceptance:* new tests in `tests/central/test_theme.py`: every page (sign-in and My work) renders `data-theme="light"` without a cookie and with an unknown cookie value; `style.css` contains no `prefers-color-scheme` and has `:root[data-theme="dark"]` with the old dark `--bg` value `#10141b`.
  *Slice:* 1 Light by default
  *Kind:* feature
- [ ] **L2 Dark mode toggle.** Add `POST /theme` (in `auth.py` or `ui.py`; CSRF as usual): takes `theme` (`light` or `dark`; anything else is 400) and `next` (through `ui.safe_next`, default `/my`), sets the `theme` cookie for one year (`Secure`, `SameSite=Lax`, path `/`) and redirects. In `base.html` add a small form in the top bar (signed in and signed out) with one button: "Dark mode" when light, "Light mode" when dark, posting the other value and the current path. Styles in `static/style.css` only; update `auto-runner/central/README.md` and the root `CHANGELOG.md` (Unreleased).
  *Acceptance:* tests in `tests/central/test_theme.py`: posting `dark` sets the cookie and the next page renders `data-theme="dark"` with a "Light mode" button; posting `light` switches back; a bad value gets 400 and no cookie; `next=//evil.example` redirects to `/my`; the form carries `csrf_token` and a POST without it gets 400; `test_pages_style.py` still passes.
  *Slice:* 2 Choose dark mode
  *Kind:* feature

## Spec check
One finding: L2 named "central README and CHANGELOG" loosely. Fixed by naming `auto-runner/central/README.md` and the
root `CHANGELOG.md`. No other findings.

## Needs a person
- After merging and deploying: open the live site and follow J1 and J2 (or ask Claude to take screenshots of a page
  in both themes against a local copy).
