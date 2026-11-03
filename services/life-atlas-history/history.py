#!/usr/bin/env python3
"""Private, offline Google Timeline + GPSLogger index. Python 3.11+, stdlib only."""
import argparse
import csv
import datetime as dt
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

MAX_BYTES = 256 * 1024 * 1024
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS assets (
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, format TEXT NOT NULL,
 sha256 TEXT NOT NULL, archived_path TEXT NOT NULL, bytes INTEGER NOT NULL,
 imported_utc TEXT NOT NULL, UNIQUE(source,format,sha256));
CREATE TABLE IF NOT EXISTS arrivals (
 asset_id INTEGER NOT NULL REFERENCES assets(id), filename TEXT NOT NULL,
 PRIMARY KEY(asset_id,filename));
CREATE TABLE IF NOT EXISTS events (
 asset_id INTEGER NOT NULL REFERENCES assets(id), locator TEXT NOT NULL,
 source TEXT NOT NULL, kind TEXT NOT NULL, start_utc TEXT NOT NULL,
 end_utc TEXT NOT NULL, latitude REAL, longitude REAL, place_id TEXT,
 activity TEXT, confidence REAL, payload_json TEXT NOT NULL,
 PRIMARY KEY(asset_id,locator));
CREATE INDEX IF NOT EXISTS event_time ON events(start_utc,end_utc);
CREATE TABLE IF NOT EXISTS points (
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, time_utc TEXT NOT NULL,
 latitude REAL NOT NULL, longitude REAL NOT NULL, identity_key TEXT NOT NULL,
 altitude_m REAL, accuracy_m REAL, speed_mps REAL, bearing_deg REAL,
 UNIQUE(source,identity_key));
CREATE INDEX IF NOT EXISTS point_time ON points(time_utc);
CREATE TABLE IF NOT EXISTS observations (
 asset_id INTEGER NOT NULL REFERENCES assets(id), locator TEXT NOT NULL,
 point_id INTEGER NOT NULL REFERENCES points(id), role TEXT NOT NULL,
 payload_json TEXT NOT NULL, PRIMARY KEY(asset_id,locator));
CREATE TABLE IF NOT EXISTS import_failures (
 filename TEXT PRIMARY KEY, error_type TEXT NOT NULL, seen_utc TEXT NOT NULL);
CREATE VIEW IF NOT EXISTS chronology AS
 SELECT source, time_utc AS start_utc, time_utc AS end_utc,
 'point' AS kind, latitude,longitude,NULL AS activity,NULL AS confidence,
 'points' AS record_table,id AS record_id FROM points
 UNION ALL
 SELECT source,start_utc,end_utc,kind,latitude,longitude,activity,confidence,
 'events',rowid FROM events;
"""


def utc(value):
    value = str(value).strip()
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed.astimezone(dt.timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def number(value):
    if value is None or str(value).strip() == '':
        return None
    result = float(str(value).replace(',', '.'))
    if not math.isfinite(result):
        raise ValueError('non-finite number')
    return result


def coordinate(value):
    if not isinstance(value, str):
        raise ValueError('coordinate string required')
    numeric = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
    match = re.fullmatch(rf'\s*({numeric})°?\s*,\s*({numeric})°?\s*', value)
    if not match:
        raise ValueError('unrecognised coordinate')
    return latlon(match[1], match[2])


def latlon(lat, lon):
    lat, lon = number(lat), number(lon)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('invalid coordinates')
    return lat, lon


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def stable_read(path, minimum_age):
    """Avoid symlinks, huge files and writes observed during capture."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise ValueError('not a supported regular file')
        if time.time() - before.st_mtime < minimum_age:
            raise BlockingIOError('upload too recent')
        with os.fdopen(fd, 'rb', closefd=False) as f:
            payload = f.read(MAX_BYTES + 1)
        after = os.fstat(fd)
        current = path.stat(follow_symlinks=False)
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise BlockingIOError('upload changed')
        if current.st_ino != before.st_ino or current.st_mtime_ns != before.st_mtime_ns:
            raise BlockingIOError('upload replaced')
        if len(payload) != before.st_size:
            raise BlockingIOError('short capture')
        return payload
    finally:
        os.close(fd)


def add_point(db, asset, source, locator, role, timestamp, lat, lon, payload,
              altitude=None, accuracy=None, speed=None, bearing=None):
    timestamp = utc(timestamp)
    lat, lon = latlon(lat, lon)
    # ~1 cm location quantisation for GPX/CSV duplicate matching only. Originals
    # and each observation remain exact in archived source + payload_json.
    identity = f'{timestamp}|{lat:.7f}|{lon:.7f}'
    values = tuple(number(v) for v in (altitude, accuracy, speed, bearing))
    db.execute('''INSERT INTO points(source,time_utc,latitude,longitude,identity_key,
        altitude_m,accuracy_m,speed_mps,bearing_deg) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source,identity_key) DO UPDATE SET
        altitude_m=COALESCE(points.altitude_m,excluded.altitude_m),
        accuracy_m=COALESCE(points.accuracy_m,excluded.accuracy_m),
        speed_mps=COALESCE(points.speed_mps,excluded.speed_mps),
        bearing_deg=COALESCE(points.bearing_deg,excluded.bearing_deg)''',
        (source, timestamp, lat, lon, identity, *values))
    pid = db.execute('SELECT id FROM points WHERE source=? AND identity_key=?', (source, identity)).fetchone()[0]
    db.execute('INSERT INTO observations VALUES (?,?,?,?,?)',
               (asset, locator, pid, role, json.dumps(payload, ensure_ascii=False)))


def import_google(db, asset, blob):
    raw = json.loads(blob)
    if not isinstance(raw.get('semanticSegments'), list):
        raise ValueError('semanticSegments required')
    for i, segment in enumerate(raw['semanticSegments']):
        start, end = utc(segment['startTime']), utc(segment['endTime'])
        if end < start:
            raise ValueError('negative interval')
        kinds = [k for k in ('visit', 'activity', 'timelinePath', 'timelineMemory') if k in segment]
        if len(kinds) != 1:
            raise ValueError('unknown Google segment type')
        kind = kinds[0]
        lat = lon = place = activity = confidence = None
        if kind == 'visit':
            candidate = segment[kind].get('topCandidate', {})
            location = candidate.get('placeLocation', {}).get('latLng')
            if location:
                lat, lon = coordinate(location)
            place = candidate.get('placeId')
            confidence = number(candidate.get('probability'))
        elif kind == 'activity':
            candidate = segment[kind].get('topCandidate', {})
            activity, confidence = candidate.get('type'), number(candidate.get('probability'))
            for edge, timestamp in [('start', start), ('end', end)]:
                location = segment[kind].get(edge, {}).get('latLng')
                if location:
                    a, b = coordinate(location)
                    add_point(db, asset, 'google_timeline', f'semanticSegments/{i}/activity/{edge}',
                              'inferred_activity_endpoint', timestamp, a, b, segment[kind][edge])
        elif kind == 'timelinePath':
            for j, point in enumerate(segment[kind]):
                a, b = coordinate(point['point'])
                add_point(db, asset, 'google_timeline', f'semanticSegments/{i}/timelinePath/{j}',
                          'timeline_path', point['time'], a, b, point)
        db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   (asset, f'semanticSegments/{i}', 'google_timeline', kind, start, end,
                    lat, lon, place, activity, confidence, json.dumps(segment, ensure_ascii=False)))
    for i, signal in enumerate(raw.get('rawSignals', [])):
        position = signal.get('position')
        if position:
            a, b = coordinate(position['LatLng'])
            add_point(db, asset, 'google_timeline', f'rawSignals/{i}/position',
                      'raw_position', position['timestamp'], a, b, position,
                      altitude=position.get('altitudeMeters'), accuracy=position.get('accuracyMeters'),
                      speed=position.get('speedMetersPerSecond'))


def import_gpx(db, asset, blob):
    # Reject DTDs explicitly; no network or custom entity resolution.
    if b'<!DOCTYPE' in blob.upper() or b'<!ENTITY' in blob.upper():
        raise ValueError('DTD not supported')
    root = ET.fromstring(blob)
    if root.tag.rsplit('}', 1)[-1] != 'gpx':
        raise ValueError('not GPX')
    for i, point in enumerate(x for x in root.iter() if x.tag.rsplit('}', 1)[-1] in ('trkpt', 'wpt', 'rtept')):
        fields = {x.tag.rsplit('}', 1)[-1]: x.text for x in point.iter() if x is not point}
        if not fields.get('time'):
            raise ValueError('point timestamp missing')
        payload = {'attributes': point.attrib, 'xml': ET.tostring(point, encoding='unicode')}
        add_point(db, asset, 'gpslogger', f'point/{i}', 'gpx', fields['time'],
                  point.attrib['lat'], point.attrib['lon'], payload,
                  altitude=fields.get('ele'), speed=fields.get('speed'), bearing=fields.get('course'))


def import_csv(db, asset, blob):
    text = blob.decode('utf-8-sig')
    if not text.endswith(('\n', '\r')):
        raise ValueError('incomplete CSV line')
    dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not {'time', 'lat', 'lon'}.issubset(reader.fieldnames or []):
        raise ValueError('unsupported CSV headers')
    for i, row in enumerate(reader):
        if None in row or any(v is None for v in row.values()):
            raise ValueError('incomplete CSV row')
        timestamp = row['time']
        if row.get('timestamp_ms'):
            milliseconds = int(row['timestamp_ms'])
            timestamp = (dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
                         + dt.timedelta(milliseconds=milliseconds)).isoformat()
        add_point(db, asset, 'gpslogger', f'row/{i}', 'csv', timestamp,
                  row['lat'], row['lon'], row, altitude=row.get('elevation'),
                  accuracy=row.get('accuracy'), speed=row.get('speed'), bearing=row.get('bearing'))


IMPORTERS = {'json': import_google, 'gpx': import_gpx, 'csv': import_csv}


def snapshot_blob(archived, blob, digest):
    """Publish only durable complete captures; interrupted writes are retryable."""
    if archived.exists() and hashlib.sha256(archived.read_bytes()).hexdigest() == digest:
        return
    fd, temporary = tempfile.mkstemp(prefix='.capture-', dir=archived.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        if archived.exists():
            # Preserve a damaged pre-existing capture rather than silently
            # discarding it or blocking every future retry of a valid source.
            archived.rename(archived.with_name(archived.name + '.corrupt-' + uuid.uuid4().hex))
            print('Recovered incomplete raw snapshot', file=sys.stderr)
        temporary.replace(archived)
        directory = os.open(archived.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def ingest(db, state, path, source, format_, minimum_age):
    blob = stable_read(path, minimum_age)
    digest = hashlib.sha256(blob).hexdigest()
    existing = db.execute('SELECT id FROM assets WHERE source=? AND format=? AND sha256=?',
                          (source, format_, digest)).fetchone()
    if existing:
        with db:
            db.execute('INSERT OR IGNORE INTO arrivals VALUES (?,?)', (existing[0], path.name))
            db.execute('DELETE FROM import_failures WHERE filename=?', (path.name,))
        return False
    # Root-private, content-addressed snapshot protects against later uploader
    # overwrites. Retain even malformed captures for explicit later inspection.
    archived = state / 'raw-snapshots' / f'{source}-{digest}.{format_}'
    snapshot_blob(archived, blob, digest)
    # Asset, observations, points and events are one atomic transaction. Failed
    # or incomplete input never becomes a successfully imported asset.
    with db:
        cursor = db.execute('INSERT INTO assets(source,format,sha256,archived_path,bytes,imported_utc) VALUES (?,?,?,?,?,?)',
                            (source, format_, digest, str(archived), len(blob), stamp()))
        aid = cursor.lastrowid
        IMPORTERS[format_](db, aid, blob)
        db.execute('INSERT INTO arrivals VALUES (?,?)', (aid, path.name))
        db.execute('DELETE FROM import_failures WHERE filename=?', (path.name,))
    return True


def summary(db):
    return {
        'generated_utc': stamp(),
        'assets': db.execute('SELECT count(*) FROM assets').fetchone()[0],
        'points': {r[0]: r[1] for r in db.execute('SELECT source,count(*) FROM points GROUP BY source')},
        'events': {r[0]: r[1] for r in db.execute('SELECT kind,count(*) FROM events GROUP BY kind')},
        'point_range_utc': db.execute('SELECT min(time_utc),max(time_utc) FROM points').fetchone(),
        'event_range_utc': db.execute('SELECT min(start_utc),max(end_utc) FROM events').fetchone(),
        'pending_failures': db.execute('SELECT filename,error_type,seen_utc FROM import_failures ORDER BY filename').fetchall(),
        'meaning': 'Source-linked observations and Google inferences; not a verified travel biography. Cross-source overlaps retained.'
    }


def run(state, google, incoming, minimum_age=10):
    os.umask(0o077)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    (state / 'raw-snapshots').mkdir(mode=0o700, exist_ok=True)
    with (state / 'import.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = sqlite3.connect(state / 'history.sqlite', timeout=30)
        try:
            db.executescript(SCHEMA)
            changed = errors = deferred = 0
            paths = [(google, 'google_timeline', 'json')]
            paths.extend((p, 'gpslogger', p.suffix[1:].lower()) for p in sorted(incoming.iterdir()) if p.suffix.lower() in ('.gpx', '.csv'))
            for path, source, format_ in paths:
                try:
                    changed += ingest(db, state, path, source, format_, minimum_age)
                except BlockingIOError:
                    deferred += 1
                except (ValueError, KeyError, TypeError, OverflowError, OSError, csv.Error, ET.ParseError) as exc:
                    errors += 1
                    # Log only filenames and exception class, never payload/coordinates.
                    with db:
                        db.execute('INSERT OR REPLACE INTO import_failures VALUES (?,?,?)',
                                   (path.name, type(exc).__name__, stamp()))
                    print(f'Import failed: {path.name}: {type(exc).__name__}', file=sys.stderr)
            report = summary(db)
            report.update(new_assets=changed, deferred_uploads=deferred)
            tmp = state / 'summary.json.tmp'
            tmp.write_text(json.dumps(report, indent=2) + '\n')
            tmp.replace(state / 'summary.json')
            print(json.dumps(report))
            return 1 if errors else 0
        finally:
            db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path('/srv/life-atlas/history'))
    parser.add_argument('--google', type=Path, default=Path('/srv/life-atlas/google-timeline/2026-11-03/Timeline.json'))
    parser.add_argument('--incoming', type=Path, default=Path('/srv/gpslogger/incoming'))
    parser.add_argument('--minimum-age', type=float, default=10)
    args = parser.parse_args()
    sys.exit(run(args.state, args.google, args.incoming, args.minimum_age))
