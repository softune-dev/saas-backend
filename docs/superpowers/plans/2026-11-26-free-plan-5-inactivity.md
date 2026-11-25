# Free plan, part 5b: deleting inactive Free stores

Status: **waiting for Kallol's approval. Nothing here is built.**
Spec: `../specs/2026-11-24-free-plan-design.md` (Inactivity).

Deletion removes a store, its products, orders and media for good. A mistake cannot be
undone, so every rule below gets its own test.

## Who can ever be deleted

Only a tenant that meets **all** of these:

- plan is `free` (never starter, growth, business, demo)
- status is `active` (a suspended account is left for the admin to decide)
- no superadmin user
- no payment waiting to be confirmed (plan claim or credit purchase)
- inactive for the full period: **30 days**, or **90 days** if it ever paid. If the records
  disagree about whether it paid, it gets 90.

Activity is what the app already records (`tenants.last_active_at`): dashboard use, login,
product edit or a received order. All clocks started when migration 087 ran (24 Nov 2026).

## Warnings

| | Warning 1 | Warning 2 | Warning 3 | Deleted |
|---|---|---|---|---|
| Never paid | day 15 | day 23 | day 29 | day 30 |
| Paid before | day 60 | day 75 | day 85 | day 90 |

- A warning counts only if the email was actually sent.
- Warning 2 is at least 5 days after warning 1, and warning 3 at least 5 days after
  warning 2. If the worker was down, the dates move later; they are never squeezed together.
- Deletion needs all three, and the third at least 24 hours old.
- Any activity cancels the count; it starts again from warning 1.

## Brakes

- At most 20 deletions in a run. If more qualify, it deletes **none** and emails you.
- Owners are saved to Contacts first. If that fails, nothing is deleted that round.
- The DELETE statement checks every condition again, so a store that pays or comes back
  while the run is in progress survives.
- A server setting, `INACTIVITY_DELETION`, with three values:
  - `off` (default): the job does nothing.
  - `report`: sends nothing and deletes nothing; logs who would be warned or deleted.
  - `on`: sends warnings and deletes.

  This is a safety catch, not a hidden feature: your PC's worker uses the live database,
  so without it, starting `python -m app.worker` locally could delete real stores. Nothing
  a visitor or merchant sees depends on it. Plan: deploy with `report`, you read the list,
  then set `on`.

## Database

Three columns, added to migration 089 (not yet run):
`inactivity_warning_stage` (0 to 3), `inactivity_warned_at`, and `inactivity_warned_for`
(the `last_active_at` the warnings belong to, so new activity voids them).

## Tests

The job takes a list of tenant ids, and tests pass only their own test tenants, so a test
can never warn or delete a real store. Email is faked. No test runs the job loop.

One test each: every excluded plan and status; pending payment; superadmin; one day short
of 30 and of 90; each warning's day and gap; a failed email does not count; activity
resets; deletion refused with two warnings, or a third one younger than 24 hours; 21
candidates delete none; contacts failure deletes none; the DELETE re-check; `off` and
`report` send and delete nothing.

## Files

`app/inactivity.py` (rules and job), `app/mailer.py` (3 warning emails),
`app/worker.py` (hourly loop), `app/config.py` (the setting), `migrations/089`,
`app/models.py`, `tests/test_inactivity.py`.
