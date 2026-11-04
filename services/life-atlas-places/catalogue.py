#!/usr/bin/env python3
"""Build an offline, derived place catalogue; never reads personal travel data."""
import argparse
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import zipfile

SCHEMA = '''
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE places (
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, object_type TEXT NOT NULL,
 object_id TEXT NOT NULL, name TEXT NOT NULL, name_en TEXT,
 category TEXT NOT NULL, subtype TEXT, latitude REAL NOT NULL, longitude REAL NOT NULL,
 country TEXT, admin1 TEXT, geometry TEXT,
 UNIQUE(source,object_type,object_id));
CREATE VIRTUAL TABLE bounds USING rtree(id,minlon,maxlon,minlat,maxlat);
'''
# Reference selection rectangles are operator input, not administrative borders.
CATEGORIES = ('tourism', 'amenity', 'shop', 'leisure', 'historic', 'natural', 'place')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def add_place(db, source, kind, oid, name, lat, lon, category, subtype='',
              name_en=None, country=None, admin1=None, geometry=None):
    if not name or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return
    if geometry:
        xy = [p for polygon in geometry['coordinates'] for ring in polygon for p in ring]
        minlon, maxlon = min(p[0] for p in xy), max(p[0] for p in xy)
        minlat, maxlat = min(p[1] for p in xy), max(p[1] for p in xy)
    else:
        minlon = maxlon = lon
        minlat = maxlat = lat
    cur = db.execute('''INSERT INTO places
        (source,object_type,object_id,name,name_en,category,subtype,latitude,longitude,
         country,admin1,geometry) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
        (source, kind, str(oid), name, name_en, category, subtype, lat, lon,
         country, admin1, json.dumps(geometry, separators=(',', ':')) if geometry else None))
    db.execute('INSERT INTO bounds VALUES (?,?,?,?,?)',
               (cur.lastrowid, minlon, maxlon, minlat, maxlat))


def import_geonames(db, path):
    with zipfile.ZipFile(path) as archive:
        with archive.open('cities1000.txt') as f:
            for line in io.TextIOWrapper(f, encoding='utf-8'):
                r = line.rstrip('\n').split('\t')
                if len(r) != 19:
                    raise ValueError('unexpected GeoNames row')
                add_place(db, 'geonames', 'populated_place', r[0], r[1], float(r[4]),
                          float(r[5]), 'locality', r[7], name_en=r[2],
                          country=r[8], admin1=r[10])


def import_osm(db, path, scratch, bbox):
    import osmium  # Isolated build-time dependency; report generation is stdlib.
    factory = osmium.geom.GeoJSONFactory()

    class Handler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.bad_geometry = 0

        def tags(self, obj):
            # Most OSM objects are unnamed; avoid allocating Python dictionaries
            # for every road/building/node in the regional extract.
            if not obj.tags.get('name'):
                return None
            for category in CATEGORIES:
                if category in obj.tags:
                    return dict(obj.tags), category
            return None

        def node(self, n):
            tagged = self.tags(n)
            if not tagged or not n.location.valid():
                return
            lat, lon = n.location.lat, n.location.lon
            if not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
                return
            tags, category = tagged
            add_place(db, 'osm', 'node', n.id, tags['name'], lat, lon,
                      category, tags[category], name_en=tags.get('name:en'))

        def area(self, a):
            tagged = self.tags(a)
            if not tagged:
                return
            try:
                geometry = json.loads(factory.create_multipolygon(a))
                xy = [p for polygon in geometry['coordinates'] for ring in polygon for p in ring]
                if not xy:
                    return
                # Bounding-box centre is a search representative, not an entrance
                # or guaranteed interior point. Matching uses the actual rings.
                minlon, maxlon = min(p[0] for p in xy), max(p[0] for p in xy)
                minlat, maxlat = min(p[1] for p in xy), max(p[1] for p in xy)
                lon = (minlon + maxlon) / 2
                lat = (minlat + maxlat) / 2
            except (RuntimeError, ValueError, KeyError):
                self.bad_geometry += 1
                return
            # Keep areas intersecting the selection even when their centre
            # lies outside; exact ring matching later rejects false candidates.
            if maxlon < bbox[0] or minlon > bbox[2] or maxlat < bbox[1] or minlat > bbox[3]:
                return
            tags, category = tagged
            add_place(db, 'osm', 'way' if a.from_way() else 'relation', a.orig_id(),
                      tags['name'], lat, lon, category, tags[category],
                      name_en=tags.get('name:en'), geometry=geometry)

    handler = Handler()
    # Disk-backed location index avoids a full in-memory node cache. Area
    # assembly still has a cost; the caller must enforce a process memory cap.
    handler.apply_file(str(path), locations=True, idx='sparse_file_array,' + str(scratch))
    return handler.bad_geometry


def build(osm_path, towns_path, output, bbox, osm_url):
    if not (-179 <= bbox[0] < bbox[2] <= 179 and -85 <= bbox[1] < bbox[3] <= 85):
        raise ValueError('pilot rectangle must not cross the antimeridian or poles')
    os.umask(0o077)
    output = Path(output)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    osm_hash, towns_hash = sha256(osm_path), sha256(towns_path)
    if output.exists():
        with closing(sqlite3.connect(output.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            meta = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM metadata')}
            expected = dict(schema_version='2', osm_sha256=osm_hash,
                            geonames_sha256=towns_hash, osm_bbox=list(bbox), osm_url=osm_url)
            if any(meta.get(k) != v for k, v in expected.items()):
                raise ValueError('existing catalogue uses different references/settings; choose a new output')
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('existing catalogue integrity failure')
            if db.execute('SELECT count(*) FROM places').fetchone() != db.execute('SELECT count(*) FROM bounds').fetchone():
                raise ValueError('existing catalogue bounds incomplete')
            return dict(places_by_source=dict(db.execute('SELECT source,count(*) FROM places GROUP BY source')),
                        skipped_geometry=meta['skipped_osm_geometries'], reused=True)
    with tempfile.TemporaryDirectory(prefix='.catalogue-', dir=output.parent) as tmp:
        dbpath = Path(tmp) / 'catalogue.sqlite'
        with closing(sqlite3.connect(dbpath)) as db, db:
            db.executescript(SCHEMA)
            import_geonames(db, towns_path)
            bad = import_osm(db, osm_path, Path(tmp) / 'node-index', bbox)
            meta = {
                'schema_version': '2', 'osm_sha256': osm_hash,
                'geonames_sha256': towns_hash, 'osm_bbox': bbox,
                'osm_url': osm_url,
                'geonames_url': 'https://download.geonames.org/export/dump/cities1000.zip',
                'attribution': '© OpenStreetMap contributors, ODbL 1.0; GeoNames, CC BY 4.0',
                'skipped_osm_geometries': bad,
                'limitations': 'Current map, not historical ground truth. Named nodes and areas only; '
                    'unnamed objects and non-area ways omitted. Nearby locality is not a boundary lookup.',
            }
            db.executemany('INSERT INTO metadata VALUES (?,?)',
                           [(k, json.dumps(v)) for k, v in meta.items()])
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('catalogue integrity failure')
            summary = dict(places_by_source=dict(db.execute(
                'SELECT source,count(*) FROM places GROUP BY source')), skipped_geometry=bad, reused=False)
        os.replace(dbpath, output)
        os.chmod(output, 0o600)
        return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--osm', required=True)
    p.add_argument('--towns', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--bbox', required=True, nargs=4, type=float, metavar=('MINLON','MINLAT','MAXLON','MAXLAT'))
    p.add_argument('--osm-url', required=True, help='public reference URL for provenance; never a lookup endpoint')
    args = p.parse_args()
    summary = build(args.osm, args.towns, args.output, args.bbox, args.osm_url)
    try:
        print(json.dumps(summary), flush=True)
    except BrokenPipeError:
        # Publication has completed; a detached observer must not turn a valid
        # catalogue into a failed job during interpreter stdout teardown.
        fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(fd, sys.stdout.fileno())
        os.close(fd)
