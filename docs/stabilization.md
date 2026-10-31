# Stabilization rollout

## Acceptance checks before merging

1. Run `python -m unittest discover -s tests -v`, `pip-audit -r requirements.txt`, and the GitHub PostgreSQL/backup smoke jobs.
2. Check `/matches`, `/matches?view=live`, `/feedback`, `/settings` and `/search` at phone and desktop widths. The score must be readable and forms must not create horizontal scrolling.
3. Confirm that an old snapshot shows its check time and does not claim to be a real-time score.
4. Verify the configured Render service waits for checks. The Blueprint now declares `autoDeployTrigger: checksPass`; manually configured services need the corresponding dashboard setting.

This release does not need a database schema migration. It does not remove match,
review, watchlist or account rows. Keep the preceding deployment available for rollback.

## Review the existing production catalog

Run `python scripts/audit_catalog.py --limit 50` using a read-only database
connection when available. Record the totals and investigate the reported match
IDs against the provider. The report contains bounded public result fields and IDs, not account data. Its SQL transaction rejects writes.

Cross-tour and self-match rows are excluded from discovery and player statistics,
and their detail pages show a notice. Existing personal entries are preserved.
This containment does not prove the correctness of every other historical result.

Before writing repairs, run:

```sh
python scripts/plan_catalog_recovery.py --limit 10 > catalog-recovery-plan.json
# Restrict a preview to a previously reported permanent match ID:
python scripts/plan_catalog_recovery.py --match-id lt-211673 --limit 1
```

The **Preview catalog recovery** workflow prepares the same JSON artifact. The
command runs inside a database-enforced read-only transaction, makes at most one
provider request per sampled result, and stops requests after the first error.
It includes original fields, the source response hash and proposed participants;
conflicting provider mappings are blocked, never globally reassigned. A 404/410
is unresolved, not evidence of cancellation. Retired `tennis_api` results require
that original source or independently verified evidence; their numeric IDs must
never be sent to the active Live Tennis API. The preview is review evidence, not
an executable migration or proof that every unflagged result is correct.

The shared identity resolver only uses a name-only profile when it is unique in
its tour and does not compete with another ID from the same provider. Distinct
IDs with the same name get distinct profiles; uncertain duplicate identities
require manual review. Namesake profile links, archive redirects and player
notifications use this same resolver. This can intentionally leave a live match
unlinked rather than attribute it to an uncertain profile.

After checking the preview and recovery prerequisites, use
`python scripts/repair_live_archive.py --limit 10` with the limited runtime
database role and the provider key to recheck a bounded set of existing IDs.
The script writes explicit provider outcomes without deleting and rebuilding
matches. It spends up to the requested number of API calls. Confirm remaining
quota before repeating it; 403/429 errors stop the run instead of retrying.

Player ID/name/tour conflicts require investigation. Do not bulk-merge players
by name, reassign provider IDs, or delete Unknown Player rows without reviewing
their dependent matches and user entries. Unknown Player records remain addressable
but are not promoted in the main directory.

## Enable and verify backups

The repository cannot establish a trustworthy production backup without the
installation's credentials and an offline encryption key held by its owner.

1. Prepare the age recipient and keep the corresponding private key offline.
2. Configure `BACKUP_RECIPIENT` (the public age recipient). Prefer a dedicated
   `BACKUP_DATABASE_URL` with read access over TLS; when absent, the workflow
   uses the existing `TENNISD_SYNC_DATABASE_URL` and enforces a read-only dump
   transaction. Keep the owner-held private key outside GitHub and the repository.
   Enable `BACKUPS_ENABLED=true` only after reviewing the data transfer: the
   complete database, including account/diary records, is read in the repository's
   GitHub Actions runner, restored into an ephemeral CI database, encrypted,
   and stored as `tennisd.dump.age` for 30 days.
3. Run **Encrypted database backup**. It must restore the dump into its isolated
   PostgreSQL service before encrypting and uploading it. A skipped run is not a backup.
4. Download the encrypted artifact, decrypt it using the offline key, and restore
   it into a fresh temporary database. Verify the table counts and a test-account
   export. Record the successful drill date outside the public repository.
5. Check the workflow after the next scheduled run. Confirm who owns failures,
   retention, key custody and periodic recovery drills.

The CI restore helper uses a fixed, empty service database and refuses to overwrite
an existing one. It never connects to production for restoration. No plaintext
dump is uploaded as an artifact.

## Next product work

These are planned validation steps, not claims of finished features:

- Interview 10–15 regular ATP/WTA viewers. Observe finding a match, logging it,
  returning a week later, and sharing a diary. Measure successful task completion
  and repeat logging rather than total imported catalog rows.
- Choose one reason to prefer Tennisd over existing tennis diaries, based on those
  interviews. Validate it with a small prototype before expanding the feature list.
- Introduce explicit tournament identity/edition aliases with a reviewed migration;
  preserve old URLs and subscriptions while merging duplicates.
- Profile heavy queries on a representative catalog; diary, likes, watchlist and friends now use bounded pagination.
- Decide whether the news section needs durable storage and a separate refresh job.
  The current cache is process-local and is rebuilt on deploy.
- Connect recommendation rules only after the underlying catalog is trustworthy;
  measure their usefulness before training a model.

## Reviewed single-result correction

The version-controlled record in `tennisd/data/result_corrections.json` documents
one independently verified exception: `lt-211673` is Koray Kirci versus **Yi Yuan
(HKG)**, not Yue Yuan (CHN). The official ITF draw and player IDs are linked in the
record. The active provider still returns the wrong opponent identity, so its
response alone is insufficient evidence for this repair.

```sh
# Default operation is a database-enforced read-only preview.
python scripts/correct_reviewed_result.py itf-sharm-2026-030-kirci-yuan
# After reviewing the before/after and official evidence:
python scripts/correct_reviewed_result.py itf-sharm-2026-030-kirci-yuan --operation apply
# Restore only the original result fields if a correction must be reversed:
python scripts/correct_reviewed_result.py itf-sharm-2026-030-kirci-yuan --operation rollback
```

The **Correct a reviewed result** workflow exposes those same operations and
stores a journal containing public result fields only. Apply/rollback lock the
permanent match, check all reviewed identity/result fields for drift, and stop
if participant/statistic rows need a broader review. They preserve the match ID,
all diary entries, and existing external mappings. The verified player gets a
separate ITF identity; the WTA profile is never renamed or reassigned. Repeating
an operation is a no-op. Rollback does not delete the newly created profile.

This is a narrow correction with a known original row and reversible field
changes. It does not enable bulk historical repair. A verified production backup
and independently checked source evidence remain prerequisites for that work.

The auto sync guard allows one refresh per UTC calendar day. It does not require
24 elapsed hours: schedule jitter or moving the cron earlier must not skip the
next day. Explicit manual `live`/`upcoming` modes still request a refresh.

## Bounded profile collections

Diary, likes, watchlist and accepted friends use 18-item pages with previous/next
navigation at both ends. Tied dates are ordered by descending primary key. Bad
page values start at page one; oversized pages are clamped after a visibility-
scoped count and before calculating the SQL offset. Tab links reset pagination.

The profile overview loads five recent reviews and four favorites. Full visible
counts, averages, rating distributions and player appearances are SQL aggregates,
not statistics of the current page. Player counts use permanent IDs so namesakes
stay separate. Public/member views include public reviews only; the owner can
see private entries. Page counts and summaries use that same visibility condition.
The watchlist and friends remain owner-only. Account export is not paginated.

No schema migration or production data repair is needed for this release. A
rollback of the app deployment restores the previous view of the same records.
