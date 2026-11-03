# Private travel-history index

An offline, standard-library Python/SQLite importer for a preserved Android
Google Timeline JSON export and GPSLogger GPX/CSV uploads. No HTTP service,
external geocoder, database server or new phone app is required.

This is a **source-linked observation index**, not a verified travel biography.
Google's inferred visits/activities/trip memories remain labelled as such.
GPSLogger contributes recorded points; it does not acquire Google's activity
recognition simply by importing these files. New trip/transport inference and
readable place-name reconciliation are deliberately not implemented here.

## Why this shape

SQLite provides transactional ingestion and unique-key handling without another
service. A full mapping product such as [Dawarich](https://dawarich.app/) can be
useful later, but its web stack is unnecessary for this private, analysis-first
step. No custom database, authentication scheme or tracking client is built.

References: [SQLite transactions](https://sqlite.org/lang_transaction.html),
[UPSERT](https://sqlite.org/lang_upsert.html),
[GPSLogger formats](https://gpslogger.app/),
[upstream CSV fields](https://github.com/mendhak/gpslogger/blob/master/gpslogger/src/main/java/com/mendhak/gpslogger/loggers/csv/CSVFileLogger.java).
No upstream implementation code is copied; tests use synthetic coordinates.

## Data model and provenance

- `assets`: hash-identified original captures, format/source and archived path.
- `arrivals`: filenames associated with those captures.
- `events`: every Google semantic segment, original JSON, times, activity,
  candidate probability and place ID where present. Visits remain intervals;
  the importer does not invent a GPS observation at a visit start.
- `points`: common UTC/latitude/longitude index across both source families,
  optionally enriched with altitude, accuracy, speed and bearing.
- `observations`: links every point occurrence to an original capture and
  zero-based source locator, role and original payload. Google activity
  endpoints, timeline paths and raw positions retain different roles here.
- `chronology`: union view for time-based access to events and points. Sort
  explicitly by `start_utc` when querying. Point/event roles remain distinguishable;
  union rows are not additive trip counts or independent confirmations.
- `import_failures`: pending file errors; payloads/coordinates aren't logged.

Repeated identical captures are skipped. Growing/replaced files create new
content-addressed snapshots. Captures are fsynced to private temporary files
before atomic publication; interrupted writes do not poison future retries.
An incomplete pre-existing capture is retained with a `.corrupt-*` suffix and
replaced from the valid input on retry. Existing observations survive a later truncated or
corrected upload; newer data does not silently erase old evidence. Use asset
hashes/import times to investigate conflicting revisions.

Point identity is **source + exact UTC timestamp (microseconds) + coordinates
rounded to seven decimal places** (about 1 cm). This deduplicates repeated
imports and matching GPX/CSV points, while retaining every observation and exact
original value. Mismatching timestamp precision/coordinates remain separate;
there is no fuzzy location matching. Google and GPSLogger overlaps are **not**
silently collapsed: their evidence/inference differs. Optional fields use the
first non-null value, not an unvalidated "best" estimate; each conflicting
observation remains available in its payload/source capture.

Google raw signals other than `position`, frequent-place profiles and all
original nested details remain in the preserved source JSON, even if not
normalised into this index. Original dates/offsets remain in source payloads.
No lifespan totals, verified-country counts or transport accuracy claims follow
from these tables.

## Operations on the server

Installed code: `/opt/life-atlas-history/` (root-managed).

Inputs (read-only to the service):

- `/srv/life-atlas/google-timeline/2026-11-03/Timeline.json`
- `/srv/gpslogger/incoming/*.gpx` and `*.csv`, top-level regular files only.

Outputs (root-private, outside web roots and Git):

- `/srv/life-atlas/history/history.sqlite`
- `/srv/life-atlas/history/raw-snapshots/` — content-addressed captures,
  including captures that fail parsing; no automatic pruning.
- `/srv/life-atlas/history/summary.json` — aggregate status, no coordinates.

The Google baseline is also snapshotted on this same server. That is provenance
preservation, **not an off-server backup**. Snapshot storage can grow with
reuploads; retention/disk monitoring needs a separate deliberate policy.

`life-atlas-history.timer` runs the oneshot importer at boot and one hour
after each completed run. No network access, private device/tmp namespaces,
read-only system, and writes restricted to the output directory. The job runs
as root with only `CAP_DAC_READ_SEARCH` to read the uploader's private files;
SSH credentials/home directories aren't accessible to it. The upload account
cannot read or alter the index or snapshots.

Files less than ten seconds old or observed changing are deferred. Malformed
GPX/CSV inputs roll back completely, are recorded as failures, and retried on the
next run without preventing independent valid inputs from importing. Service
returns nonzero if any inputs fail; check both systemd status and summary.
Capture size is limited to 256 MiB, symlink inputs and DTDs rejected. Input
syntax and bounds checks are not proof of location accuracy.

```sh
ssh aether systemctl status life-atlas-history.timer
ssh aether systemctl start life-atlas-history.service
ssh aether journalctl -u life-atlas-history.service -n 10 --no-pager
```

For private analysis use Python's `sqlite3` over trusted SSH; **do not put a
public web interface or expose this database**. Example aggregate query:

```python
import sqlite3
with sqlite3.connect('file:/srv/life-atlas/history/history.sqlite?mode=ro', uri=True) as db:
    print(db.execute('SELECT source,count(*) FROM points GROUP BY source').fetchall())
```

Stop automatic updates without removing data:

```sh
ssh aether systemctl disable --now life-atlas-history.timer
```

No retention deletions, raw-source modifications, phone configuration changes
or remote-account integrations are performed by the importer.

## Development and deployment

```sh
python3 -B -m unittest discover -s services/life-atlas-history -v
```

Only code, service units and synthetic tests belong in Git. Do not commit raw
exports, SQLite files, upload keys or generated summaries. Deploy from this
source directory via `python3 deploy.py` **only after explicit authorization**.
Deployment uses existing trusted `ssh aether`, installs source/units, validates
and runs tests on the server, enables the systemd timer, then performs an initial
import. A malformed initial upload still reports failure, but the timer remains
enabled so a completed upload is retried automatically. It does not restart Docker, SSH or other application services.
