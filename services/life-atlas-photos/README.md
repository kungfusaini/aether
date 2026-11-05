# Private photo metadata pilot

A manually triggered, date-bounded collector and separate private SQLite index.
Not a photo backup, tracking app, scheduled job, stable Google API or AI image
analyser. Does not modify Google Photos, original files or the existing travel
history/importer. No server-side Google session.

## Boundaries and provenance

- Node 22+ attaches to an **explicit, dedicated local** Google Photos browser
  session. Its private JSON session file contains a loopback debugging `port`
  and optionally `pilotTargetId`; it is not a cookie export. Do not attach an
  everyday browser with unrelated accounts/tabs or expose the debugging port.
- Sign-in/security challenges require the human. Closed/expired sessions fail;
  the collector does not bypass challenges, refresh stolen credentials or
  silently publish an empty/partial success. Browser launch remains separate.
- Explicit `--start` and `--end` calendar dates, maximum 31 days. Queries get a
  one-day metadata buffer on either side. Retain a record if **UTC OR its
  recorded-offset calendar date** lies in the requested window. Preserve both.
- Pagination applies only to those date searches, with bounded pages/records.
  No whole-library or album enumeration is implemented here. Completed search
  pages do **not** prove complete library, shared-album or trip coverage.
- Unknown timing stays unresolved, not filled from item creation time. Google
  timestamps and original-file tag presence are evidence, not event dates or
  messaging receipt times. A filename pattern is only a transport hint.
- Only explicitly projected metadata leaves the browser. No image bytes,
  thumbnail/download URLs, cookies, session IDs, account identities or anti-CSRF
  tokens appear in the snapshot. The data itself remains private.
- Original-file audit attachments read **existing** local files/manifests only
  (maximum ten). They verify file hashes, freshly run ExifTool on private copies
  of those verified bytes, and join to the active browser's explicitly selected
  sample. External/stale EXIF JSON reports are not accepted. No further originals
  are downloaded. Native
  original retrieval and [ExifTool](https://exiftool.org/) inspection require
  their own bounded approval. A Google-served original need not be the camera
  original, and basic EXIF does not imply capture dates/GPS are present.

The request/response format was reviewed against the MIT
[Google-Photos-Toolkit](https://github.com/xob0t/Google-Photos-Toolkit) reader;
its bundle, mutations and bulk-operation UI are not installed or executed.
Protocol facts and minimal request structure are reused; attribution/license
is in `third-party/Google-Photos-Toolkit.LICENSE`. The internal website endpoint
can change without notice. Google Photos API restrictions are documented
[here](https://developers.google.com/photos/support/updates); this tool is not
an OAuth/API replacement with a support guarantee.

## One-command bounded metadata import

After an agent has opened the dedicated local browser and the user has completed
any required sign-in, run from this repository (development CLI, no global
installation or NixOS changes):

```sh
set -o pipefail
node services/life-atlas-photos/collect.mjs \
  --session "$PRIVATE_SESSION_FILE" --start "$START" --end "$END" \
  | ssh aether 'python3 /opt/life-atlas-photos/index.py ingest --db /srv/life-atlas/photos/photos.sqlite'
```

This is one manual import command **with a live browser session**, not an
unattended service. No private data is written into the repository. Do not paste
snapshot stdout into chat or logs. Diagnostics contain counts only. To attach an
already-approved original-file audit, add `--audit-manifest "$MANIFEST"`
(and optionally `--exiftool "$EXIFTOOL_BINARY"`). ExifTool must be available
locally, for example through an isolated `nix shell nixpkgs#exiftool` environment.
Originals stay local; freshly read embedded tags, provider identities, file hashes
and audit flags enter the private index. An expired page loses its temporary sample selection and
must not be guessed/rebound to existing files.

The receiver uses Python 3 stdlib/SQLite JSON functions. Deploy only `index.py`
to `/opt/life-atlas-photos/index.py` as root-owned code after validation/review.
The private data directory is `/srv/life-atlas/photos/`, mode 0700, database
0600. No daemon/timer and no modification to the raw travel database.

## Index semantics

- `assets`: stable provider identities, not assumed global content identities.
- `runs`: complete bounded query provenance and canonical snapshot hash.
- `observations`: append per-run provider facts; do not overwrite older values.
- `file_audits`: sample-specific raw embedded tags and original-file hashes.
  No originals archived by the index; never generalise a sample to a whole batch.
  `capture_date_present` means a `DateTimeOriginal` tag exists, not that it is
  correct. Creation/digitization tags have a separate flag and are not capture
  evidence. GPS presence requires both latitude and longitude tags.
- `event_links`: separate proposed/confirmed/rejected interpretations, initially
  empty. Ingestion makes **zero** automatic event/date/location associations.
- `review_groups`: UTC/recorded-offset date groups with counts of audited files
  and those missing capture dates. Supports later batch review without pretending
  all group members have the same transport, metadata or event date.

Identical replay of a run is idempotent. Reusing its identity for different facts
is rejected. Failed imports are transactional. The receiver validates date math,
query completeness, field projections and sample bounds, and refuses another
application's database. No private snapshot, index, audit report, original,
thumbnail or runtime/session file belongs in Git.

## Focused checks

```sh
node services/life-atlas-photos/test_collector.mjs
node services/life-atlas-photos/test_audit.mjs
(cd services/life-atlas-photos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_index.py)
```

Fixtures are synthetic. No Google login, private files, images or external
network calls are used. Coverage includes midnight boundaries, offsets, missing
times, pagination, refusal/no retry, privacy projection, immutable observations,
idempotence, validation, permissions, foreign-database refusal and rollback.
