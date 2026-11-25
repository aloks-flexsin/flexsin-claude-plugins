# Plan: attach screenshots to tasks (central task #4)

## Intent
Everyone working on a project can attach screenshots to a task (when adding it and later), see them on the task page,
and the runner that plans and builds the task gets them too, so it can look at what the person means. Success: a
person pastes or picks a screenshot on the task page, it appears as a thumbnail that opens full size, and the runner's
plan for that task refers to the image.
Out of scope: other file types (PDFs, videos), editing images, comments on images, and attachments on blockers.

## Context (what exists to build on)
- Task pages: `tasks.py` (`task_add`, `_detail`, `task_new`), `templates/task.html`, `templates/task_new.html`.
- Permissions: `access.project_access(role)`; tasks are added by people who can run runners (developer and above)
  or admins; every member can see every task.
- Database: `schema.sql` (`CREATE TABLE IF NOT EXISTS`), `db.init_db`; one schema for SQLite (tests) and Postgres.
  Since #28, a missing table answers 503 "needs a database update" instead of crashing.
- CSP `img-src 'self'`: images must come from this site; `static/app.js` holds all behaviour; pages work without JS.
- Task API: `GET /api/v1/tasks/<N>` (personal token), used by `autotasks.py next/start`.
- Hosting: serverless functions on Vercel, whose request body limit is 4.5 MB; no persistent disk.

## Key decisions
- **Store images in the database** (a `task_attachment` table with the bytes in a `BYTEA` column; SQLite accepts the
  same declaration). No storage service, keys or extra cost; backups come with the database. Object storage
  (Supabase Storage, S3) is the better choice only for large volumes; not needed for screenshots.
- **Limits:** PNG, JPEG, GIF or WebP only, recognised by their first bytes (not the file name); at most 4 MB each and 4 MB per
  request in total (under Vercel's 4.5 MB request limit), and 10 per task. A larger request gets 413 with the limit in
  words, from `MAX_CONTENT_LENGTH`, never a platform error.
- **Who:** any member except viewers can attach (the people who work on tasks); the uploader or an admin can delete,
  with a confirmation. Every member can view.
- **Serving:** `GET .../attachments/<id>` for members only, with the stored content type, `nosniff`,
  `Content-Disposition: inline`, and a private cache header.
- **Paste and drag-and-drop** with JavaScript, on top of a plain file input that works without it.
- **The runner gets the images:** the task API lists them, and `autotasks.py next` downloads them to
  `.auto/run/tasks/<N>/attachments/`, naming the paths in what it prints, so the planning Claude can open them.

## Slices
1. **Attach and see screenshots on the task page.**
2. **Attach while adding a task, by paste or drag-and-drop.**
3. **The runner sees the screenshots** when it plans or builds the task.

## Journeys
- **J1 Attach a screenshot to a task.** Slice 1. Roles: developer.
  1. As developer, open a task, choose a PNG under "Screenshots" and press Attach.
  *Expect:* a thumbnail appears; clicking it opens the full image; a viewer sees it too but has no Attach button.
- **J2 Paste one into a new task.** Slice 2. Roles: developer.
  1. As developer, open New task, paste a screenshot from the clipboard, fill the title and add the task.
  *Expect:* the new task's page shows the screenshot.
- **J3 The runner gets it.** Slice 3. Roles: developer.
  1. As developer, run `/auto-runner:auto` for a task with a screenshot.
  *Expect:* the output names the downloaded file under `.auto/run/tasks/<N>/attachments/`.

## Reuse check
| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Storing images | the existing Postgres database (`BYTEA`) | free (counts toward database size) | n/a | reuse | no new service or keys; small images; Vercel has no disk |
| | Supabase Storage / S3 | free tiers with limits, then paid | proprietary services | not now | worth it for large volumes; needs keys and a second place to back up |
| Recognising image types | first bytes (magic numbers) checked in a few lines | free | n/a | build | four formats; avoids a dependency such as Pillow |
| Paste and drag-and-drop | browser Clipboard and DataTransfer APIs | free | n/a | build (small) | a few lines in `app.js` |

## End to end
- Data: one new table, created by `python -m central init-db`.
- Security: members only for viewing; viewers cannot upload; type checked from the bytes; served with the stored
  type and `nosniff`; size and count limits; CSRF on uploads and deletes; deletion audited.
- Errors: a refused file says why (type, size, count) and keeps the rest of the form.
- Rollback: the table is additive; removing the routes leaves data in place.
- Docs: `auto-runner/central/README.md`, the guide, the root `CHANGELOG.md`.

## Queue items
- [ ] **S1 Store and serve task screenshots.** Add a `task_attachment` table to `schema.sql` (id, project_id, task_number, account_id, filename, content_type, size, data BYTEA, created_at) and `central/attachments.py`: `add(db, project, number, account, filename, data, now)` that checks the first bytes (PNG, JPEG, GIF, WebP), the 4 MB size and the 10-per-task limit; `for_task`; `get`; `delete` (uploader or admin). Set `app.config['MAX_CONTENT_LENGTH']` to 4 MB with a 413 handler whose page (or JSON on `/api/`) names the limit. Routes: `POST /projects/<slug>/tasks/<N>/attachments` (multipart `file`, one or more; members except viewers; CSRF), `GET /projects/<slug>/tasks/<N>/attachments/<id>` (members; stored content type, `X-Content-Type-Options: nosniff`, `Content-Disposition: inline`, `Cache-Control: private`), `POST .../attachments/<id>/delete` (confirmation, audited). Update `auto-runner/central/README.md`.
  *Acceptance:* `tests/central/test_attachments.py`: a PNG is stored and served back byte for byte with `image/png`; a text file named `x.png` is refused; 4 MB + 1 byte is refused; a request over 4 MB in total gets 413 naming the limit; the eleventh is refused; a viewer can view but gets 403 on upload; a stranger gets 404; delete needs confirmation and only the uploader or an admin can; `init-db` adds the table to an existing database.
  *Slice:* 1 Attach and see screenshots on the task page
  *Kind:* feature
- [ ] **S2 Screenshots on the task page.** In `templates/task.html`, a "Screenshots" section: thumbnails (the image itself, sized by CSS) linking to the full image, filename and uploader, a delete button under "⋯" for the uploader and admins, and an upload form (`enctype="multipart/form-data"`, `accept="image/png,image/jpeg,image/gif,image/webp"`, `multiple`) for members except viewers, with the limits in words. Styles in `static/style.css` only. Update `auto-runner/central/README.md`.
  *Acceptance:* `tests/central/test_attachments.py`: the task page shows the thumbnail and link, viewers see no upload form, errors name the reason and keep the page usable, `tests/central/test_pages_style.py` passes.
  *Slice:* 1 Attach and see screenshots on the task page
  *Kind:* feature
- [ ] **S3 Attach while adding a task; paste and drag-and-drop.** `templates/task_new.html` gets the same file input; the upload forms in `task_new.html` and `task.html` both carry `enctype="multipart/form-data"` and `data-paste-images`; `tasks.task_add` stores the files after creating the task (a refused file does not lose the task: the task page says which file was refused and why). In `static/app.js`, forms with `data-paste-images` accept pasted or dropped images into their file input (via `DataTransfer`) and list the chosen files. Update `auto-runner/central/README.md`.
  *Acceptance:* tests in `tests/central/test_attachments.py`: adding a task with two PNGs stores both; a bad file with a good one keeps the task and the good file and names the bad one; the New task page and the task page HTML each contain `enctype="multipart/form-data"` and `data-paste-images`; `app.js` contains the paste and drop handlers for `data-paste-images`.
  *Slice:* 2 Attach while adding a task, by paste or drag-and-drop
  *Kind:* feature
- [ ] **S4 The runner gets the screenshots.** `GET /api/v1/tasks/<N>` lists `attachments` (id, filename, content_type, size) and `GET /api/v1/tasks/<N>/attachments/<id>` returns the bytes (personal token, same project rules). In `auto-runner/bin/autotasks.py`, `next` downloads a task's attachments to `.auto/run/tasks/<N>/attachments/` as `<id>-<basename>` (the basename with anything outside letters, digits, `.`, `-` and `_` replaced by `_`, so a name can never leave the folder or collide), skipping files already there and prints their paths under "Screenshots"; `commands/auto.md` tells the planning step to open them. Update `auto-runner/README.md`, `auto-runner/central/README.md`, `auto-runner/docs/central-guide.md` and the root `CHANGELOG.md`, marked unverified until used live.
  *Acceptance:* `tests/central/test_attachments.py` (API listing and download, another project's token refused) and `tests/auto_runner/test_central_tasks.py` with `fake_central.py` (`next` writes the files and prints their paths; a second `next` does not download again; a filename like `../evil.png` is written inside the attachments folder); `python -m pytest -q` passes.
  *Slice:* 3 The runner sees the screenshots
  *Kind:* feature

## Spec check
One run, 3 findings, all fixed: a 4 MB limit per request with a 413 that names it (S1); both upload forms carry
`enctype="multipart/form-data"` and `data-paste-images` (S3); downloaded files are named `<id>-<safe basename>` so a
name cannot leave the folder (S4).

## Needs a person
- After merging: run `python -m central init-db` (one new table, `task_attachment`), then follow J1–J3 on the live
  service.
- Storage grows with the database: about 0.5 MB per typical screenshot. If it grows large, move the bytes to object
  storage later; the table keeps the same shape apart from the `data` column.
