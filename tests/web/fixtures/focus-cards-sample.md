**The invoice migration is written and all 42 tests pass, but it needs a backfill before deploy.**

**→ New column.** `invoices.due_on` is added as nullable, so existing rows keep working during the rollout.

**→ Backfill is required before the NOT NULL step.** Rows created before 2024 have no due date; the second migration fails on them until the backfill runs.

**1 →** Run the migration on staging.

**2 →** Run the backfill script and check the row count:

```sh
python scripts/backfill_due_on.py --dry-run
```

**3 →** Apply the NOT NULL migration.

| Step | Reversible |
| --- | --- |
| Add column | Yes |
| NOT NULL | Only with a new migration |

**Also found:** two unused imports in `billing/models.py`.

Should the backfill use the invoice date or the order date for old rows?
