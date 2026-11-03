# Tournament-name review

Start with a read-only address-collision inventory:

```sh
python scripts/audit_tournament_names.py --limit 50
```

Use the existing runtime database configuration. The CLI disables bootstrap and
enforces a read-only database transaction. It selects aggregated public catalog
metadata only, not account, diary, subscription or image data. The group limit is
1–500; `has_more` distinguishes a truncated report. Raise the limit for further
inspection; there is no cursor in this first version. Aggregation examines all
visible tour/name pairs, not only the returned groups, and does not load matches
as ORM objects. Variant metadata inside a group is complete, not independently
capped by the group limit.

The report lists names sharing a normalized URL **within the same tour**.
Counts/date ranges describe the public visibility policy, so hidden-only archives
are absent. Explicit slugs allow each variant to be inspected without guessing.

This is a first review tool, not an alias migration. Different names with
different slugs are not detected. A matching slug, overlapping years or similar
spelling does not establish shared tournament identity. Confirm official event
and edition IDs and source coverage before proposing aliases. A future migration
must separately review archived results, legacy URLs and duplicate subscriptions,
with preview, rollback and regression coverage. No current records are merged.
