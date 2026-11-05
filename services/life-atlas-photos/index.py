#!/usr/bin/env python3
"""Private append-only provider observations; event interpretations stay separate."""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys

RECORD_KEYS = {'source_id', 'provider_dedup_key', 'photo_timestamp_ms', 'recorded_offset_ms',
               'item_creation_timestamp_ms', 'utc_date', 'recorded_offset_date',
               'matching_days', 'query_days', 'media_type', 'timing_unresolved'}
AUDIT_KEYS = {'source_id', 'filename', 'sha256', 'bytes', 'provider_size_matches',
              'whatsapp_filename_hint', 'capture_date_present', 'creation_or_digitization_date_present', 'timezone_present',
              'gps_present', 'camera_present', 'embedded_tags', 'originals_archived'}
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, collected_at TEXT NOT NULL, window_json TEXT NOT NULL, queries_json TEXT NOT NULL,
 snapshot_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (source_id TEXT PRIMARY KEY, provider TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations (
 run_id TEXT NOT NULL REFERENCES runs, source_id TEXT NOT NULL REFERENCES assets,
 unresolved INTEGER NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(run_id,source_id)
);
CREATE TABLE IF NOT EXISTS file_audits (
 run_id TEXT NOT NULL, source_id TEXT NOT NULL, data_json TEXT NOT NULL,
 PRIMARY KEY(run_id,source_id), FOREIGN KEY(run_id,source_id) REFERENCES observations(run_id,source_id)
);
CREATE TABLE IF NOT EXISTS event_links (
 link_id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES assets,
 status TEXT NOT NULL CHECK(status IN ('proposed','confirmed','rejected')),
 event_start TEXT, event_end TEXT, place_reference TEXT, evidence_json TEXT NOT NULL,
 recorded_at TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS review_groups AS
 SELECT o.run_id, json_extract(o.data_json,'$.utc_date') AS utc_date,
 json_extract(o.data_json,'$.recorded_offset_date') AS recorded_offset_date,
 count(*) AS records, count(a.source_id) AS originals_audited,
 sum(CASE WHEN a.source_id IS NOT NULL AND json_extract(a.data_json,'$.capture_date_present')=0 THEN 1 ELSE 0 END) AS audited_without_capture_time
 FROM observations o LEFT JOIN file_audits a USING(run_id,source_id)
 GROUP BY o.run_id,utc_date,recorded_offset_date;
"""


def validate(snapshot):
    allowed = {'schema_version', 'provider', 'run_id', 'collected_at', 'window', 'records', 'unresolved', 'queries', 'audits'}
    if not isinstance(snapshot, dict) or set(snapshot) != allowed or snapshot['schema_version'] != 1 or snapshot['provider'] != 'google_photos':
        raise ValueError('Unsupported snapshot structure')
    window = snapshot['window']
    if set(window) != {'start', 'end', 'query_start', 'query_end', 'policy'} or window['policy'] != 'utc-or-recorded-offset-v1':
        raise ValueError('Unsupported inclusion policy')
    start, end = dt.date.fromisoformat(window['start']), dt.date.fromisoformat(window['end'])
    if str(start) != window['start'] or str(end) != window['end'] or not 0 <= (end-start).days <= 30 or window['query_start'] != str(start-dt.timedelta(days=1)) or window['query_end'] != str(end+dt.timedelta(days=1)):
        raise ValueError('Invalid bounded window')
    expected = [str(start-dt.timedelta(days=1)+dt.timedelta(days=n)) for n in range((end-start).days+3)]
    if [q.get('day') for q in snapshot['queries']] != expected or any(set(q) != {'day', 'pages', 'excluded_known_outside', 'complete'} or q['complete'] is not True or not 1 <= q['pages'] <= 20 for q in snapshot['queries']):
        raise ValueError('Incomplete date-query evidence')
    if not isinstance(snapshot['run_id'], str) or not snapshot['run_id'] or not isinstance(snapshot['collected_at'], str):
        raise ValueError('Missing run identity')
    identities = set()
    for unresolved, rows in [(False, snapshot['records']), (True, snapshot['unresolved'])]:
        if not isinstance(rows, list):
            raise ValueError('Invalid record collection')
        for row in rows:
            if not isinstance(row, dict) or set(row) != RECORD_KEYS or not isinstance(row['source_id'], str) or not row['source_id'] or row['source_id'] in identities:
                raise ValueError('Invalid or duplicated provider identity')
            if not re.fullmatch(r'[A-Za-z0-9_+=/\-]{1,1024}', row['source_id']) or row['provider_dedup_key'] is not None and (not isinstance(row['provider_dedup_key'], str) or not re.fullmatch(r'[A-Za-z0-9_+=/\-]{1,1024}', row['provider_dedup_key'])):
                raise ValueError('Invalid provider identifier type')
            identities.add(row['source_id'])
            if row['media_type'] not in ('photo', 'video') or row['timing_unresolved'] is not unresolved:
                raise ValueError('Invalid media/timing status')
            for key in ('photo_timestamp_ms', 'item_creation_timestamp_ms', 'recorded_offset_ms'):
                value = row[key]
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or abs(value) > 8640000000000000 or not math.isfinite(value) or value != math.trunc(value)):
                    raise ValueError('Invalid source time')
                if value is not None and key != 'recorded_offset_ms' and not -62135596800000 <= value <= 253402300799999:
                    raise ValueError('Source timestamp outside supported calendar')
            t, offset = row['photo_timestamp_ms'], row['recorded_offset_ms']
            if offset is not None and abs(offset) > 14*3600000:
                raise ValueError('Invalid recorded offset')
            utc = dt.datetime.fromtimestamp(t/1000, dt.timezone.utc).date().isoformat() if t is not None else None
            local = dt.datetime.fromtimestamp((t+offset)/1000, dt.timezone.utc).date().isoformat() if t is not None and offset is not None else None
            days = sorted({d for d in (utc, local) if d is not None and window['start'] <= d <= window['end']})
            if row['utc_date'] != utc or row['recorded_offset_date'] != local or row['matching_days'] != days or bool(days) is unresolved:
                raise ValueError('Date representation/inclusion mismatch')
            if not row['query_days'] or len(set(row['query_days'])) != len(row['query_days']) or any(d not in expected for d in row['query_days']):
                raise ValueError('Invalid query provenance')
    if not isinstance(snapshot['audits'], list) or len(identities) > 20000 or len(snapshot['audits']) > 10:
        raise ValueError('Snapshot safety bound exceeded')
    audited = set()
    for audit in snapshot['audits']:
        if set(audit) != AUDIT_KEYS or audit['source_id'] not in identities or audit['source_id'] in audited or audit['originals_archived'] is not False:
            raise ValueError('Invalid original-file audit identity')
        audited.add(audit['source_id'])
        if not isinstance(audit['embedded_tags'], dict) or len(audit['sha256']) != 64 or not all(c in '0123456789abcdef' for c in audit['sha256']):
            raise ValueError('Invalid original-file evidence')
        names = {key.split(':')[-1] for key in audit['embedded_tags']}
        expected_flags = {
            'capture_date_present': 'DateTimeOriginal' in names,
            'creation_or_digitization_date_present': bool(names & {'CreateDate', 'DateTimeDigitized', 'CreationDate', 'DateCreated'}),
            'timezone_present': bool(names & {'OffsetTimeOriginal', 'OffsetTimeDigitized', 'OffsetTime', 'TimeZoneOffset'}),
            'gps_present': {'GPSLatitude', 'GPSLongitude'} <= names,
            'camera_present': bool(names & {'Make', 'Model'}),
        }
        if any(audit[key] is not value for key, value in expected_flags.items()):
            raise ValueError('Audit flags disagree with tag-specific evidence')
    return snapshot


def ingest(database, snapshot):
    validate(snapshot)  # Fail before creating or changing any database.
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    os.umask(0o077)
    database = Path(database)
    if database.is_symlink() or database.parent.is_symlink():
        raise ValueError('Refusing symlinked index location')
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if database.parent.stat().st_mode & 0o077:
        raise ValueError('Index directory must be private')
    with sqlite3.connect(database) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=5000')
        app_id = db.execute('PRAGMA application_id').fetchone()[0]
        existing_tables = db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if app_id != 0x4C415048 and (app_id != 0 or existing_tables):
            raise ValueError('Refusing a database belonging to another application')
        db.execute('PRAGMA application_id=1279348808')
        db.executescript(SCHEMA)
        os.chmod(database, 0o600)
        previous = db.execute('SELECT snapshot_sha256 FROM runs WHERE run_id=?', (snapshot['run_id'],)).fetchone()
        if previous:
            if previous[0] != digest:
                raise ValueError('Run identity reused for different observations')
            return {'run_id': snapshot['run_id'], 'already_present': True, 'records': len(snapshot['records']), 'audited_originals': len(snapshot['audits'])}
        with db:
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?)', (snapshot['run_id'], snapshot['collected_at'], json.dumps(snapshot['window']), json.dumps(snapshot['queries']), digest))
            for unresolved, rows in [(False, snapshot['records']), (True, snapshot['unresolved'])]:
                for row in rows:
                    db.execute('INSERT OR IGNORE INTO assets VALUES (?,?)', (row['source_id'], 'google_photos'))
                    db.execute('INSERT INTO observations VALUES (?,?,?,?)', (snapshot['run_id'], row['source_id'], unresolved, json.dumps(row)))
            for audit in snapshot['audits']:
                db.execute('INSERT INTO file_audits VALUES (?,?,?)', (snapshot['run_id'], audit['source_id'], json.dumps(audit)))
        return {'run_id': snapshot['run_id'], 'records': len(snapshot['records']), 'unresolved': len(snapshot['unresolved']), 'audited_originals': len(snapshot['audits']), 'event_links_created': 0}


def load_snapshot(text):
    def reject_constant(_value):
        raise ValueError('Non-standard JSON constant')
    return json.loads(text, parse_constant=reject_constant)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['ingest'])
    parser.add_argument('--db', required=True)
    args = parser.parse_args()
    try:
        text = sys.stdin.read(32*1024*1024+1)
        if len(text) > 32*1024*1024:
            raise ValueError('Snapshot too large')
        print(json.dumps(ingest(args.db, load_snapshot(text))))
    except (ValueError, TypeError, KeyError, OverflowError, OSError, sqlite3.Error):
        print('Private photo index rejected the snapshot; no incomplete run published.', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
