# Engineering principles

How work is planned and built in unattended runs. The planner reads this file before it writes a plan, and every
iteration gets it in its prompt. A project can replace it with its own file (`"principles": {"file": "..."}` in
`.claude/auto.json`) or turn it off (`"principles": {"enabled": false}`).

## 1. Build in vertical slices

- Split every task into slices: Slice 1, Slice 2, Slice 3, and so on. A slice is a thin piece of the feature that runs end
  to end, through every layer it needs (UI, API, logic, data). It is not a layer on its own, such as "all the database
  tables first".
- Every slice ends in a state someone could push, deploy and use: the app builds, the tests pass, and a person can try the
  new behaviour. Slice 1 is the smallest useful version. Later slices add to it.
- A slice may take several queue items. The items of one slice come before the next slice's, and the slice's last item
  is the one that makes it usable. Its *Acceptance:* line proves a behaviour a user can see (a test that goes through the
  real entry point: a page, an endpoint, a command), not only that a part exists.
- Never leave the product broken or half-wired between items. If something cannot be finished in this slice, hide it
  behind a flag or leave it out. Do not leave a dead button or an endpoint that returns nothing.
- Order slices by value and risk: what users need first, and what proves the hard part early.

## 2. Use what exists before you build

- First use what the project already has: its components, helpers and patterns. If it has a UI library (for example
  shadcn/ui), build screens from that library's components. Do not write new buttons, dialogs or tables.
- Next, use a well-established open-source library or package before writing your own code. Do not write your own date
  handling, validation, auth, parsing, retry logic, charts or UI components when a mature package does it.
- Choose packages that are maintained (recent releases, active issues), widely used and permissively licensed (MIT,
  Apache-2.0, BSD, ISC). Name any copyleft licence (GPL, AGPL) as a question for a person instead of adding it. Add it the
  project's way (its package manager and lock file) at a pinned or ranged version, as the project does.
- Build it yourself only when nothing suitable exists, or the dependency would cost more than the code. Say why in the
  commit message.
- Never copy code from another project or website into this one unless its licence allows it and the source is named.
