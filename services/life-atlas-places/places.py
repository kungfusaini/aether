#!/usr/bin/env python3
"""Offline, read-only travel analysis. No network, web server or importer changes."""
import argparse
from collections import defaultdict
from contextlib import contextmanager
import datetime as dt
import hashlib
import html
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from zoneinfo import ZoneInfo

R = 6371008.8
DEFAULTS = dict(radius_m=75.0, minimum_seconds=600, maximum_gap_seconds=600,
                maximum_accuracy_m=100.0, minimum_points=3)


@contextmanager
def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        yield db
    finally:
        db.close()


def timestamp(value):
    t = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('timezone required')
    return t


def distance(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 2 * R * math.asin(min(1, math.sqrt(h)))


def point_in_ring(lat, lon, ring):
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        x1, y1 = a[:2]
        x2, y2 = b[:2]
        if (y1 > lat) != (y2 > lat):
            x = (x2-x1) * (lat-y1) / (y2-y1) + x1
            if lon < x:
                inside = not inside
    return inside


def contains(geometry, lat, lon):
    return any(point_in_ring(lat, lon, p[0]) and
               not any(point_in_ring(lat, lon, hole) for hole in p[1:])
               for p in geometry['coordinates'])


def edge_distance(geometry, lat, lon):
    """Local tangent-plane distance to polygon rings; regional candidate ranking only."""
    scale_x = R * math.cos(math.radians(lat)) * math.pi / 180
    scale_y = R * math.pi / 180
    best = float('inf')
    for polygon in geometry['coordinates']:
        for ring in polygon:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                ax, ay = (a[0]-lon)*scale_x, (a[1]-lat)*scale_y
                bx, by = (b[0]-lon)*scale_x, (b[1]-lat)*scale_y
                dx, dy = bx-ax, by-ay
                f = max(0, min(1, -(ax*dx + ay*dy)/(dx*dx + dy*dy))) if dx or dy else 0
                best = min(best, math.hypot(ax + f*dx, ay + f*dy))
    return best


def detect_stops(points, **settings):
    cfg = DEFAULTS | settings
    if any(v <= 0 for v in cfg.values()) or cfg['minimum_points'] < 2:
        raise ValueError('positive stop parameters and at least two points required')
    stops, cluster = [], []
    previous_time = None
    rejected = 0

    def finish(reason):
        nonlocal cluster
        if len(cluster) >= cfg['minimum_points']:
            elapsed = (timestamp(cluster[-1]['time_utc']) - timestamp(cluster[0]['time_utc'])).total_seconds()
            if elapsed >= cfg['minimum_seconds']:
                # Arithmetic centre is suitable for this pilot, not worldwide
                # antimeridian/polar tracks. Decline such clusters below.
                lat = sum(p['latitude'] for p in cluster) / len(cluster)
                lon = sum(p['longitude'] for p in cluster) / len(cluster)
                accuracies = [p['accuracy_m'] for p in cluster if p.get('accuracy_m') is not None]
                stops.append(dict(source='gpslogger', kind='inferred_stop',
                    start_utc=cluster[0]['time_utc'], end_utc=cluster[-1]['time_utc'],
                    latitude=lat, longitude=lon, point_ids=[p['id'] for p in cluster],
                    point_count=len(cluster), observed_span_seconds=elapsed,
                    max_accuracy_m=max(accuracies) if accuracies else None,
                    missing_accuracy_points=sum(p.get('accuracy_m') is None for p in cluster),
                    dispersion_m=max(distance((lat, lon), (p['latitude'], p['longitude'])) for p in cluster),
                    ends_because=reason, status='inferred; not confirmed attendance'))
        cluster = []

    for point in points:
        t = timestamp(point['time_utc'])
        if previous_time is not None and t < previous_time:
            raise ValueError('points must be chronological')
        if previous_time is not None and (t-previous_time).total_seconds() > cfg['maximum_gap_seconds']:
            finish('observation gap')
        previous_time = t
        accuracy = point.get('accuracy_m')
        valid = all(math.isfinite(point[k]) for k in ('latitude', 'longitude'))
        valid = valid and abs(point['latitude']) <= 85 and abs(point['longitude']) <= 179
        valid = valid and (accuracy is None or math.isfinite(accuracy) and 0 <= accuracy <= cfg['maximum_accuracy_m'])
        if not valid:
            rejected += 1
            finish('unusable observation')
            continue
        # Anchor plus reported uncertainty is conservative: accept only points
        # whose centre displacement + both accuracy radii fit the stop radius.
        # Missing accuracy remains explicitly unknown, never imputed as measured 0.
        if cluster:
            anchor = cluster[0]
            displacement = distance((anchor['latitude'], anchor['longitude']),
                                    (point['latitude'], point['longitude']))
            uncertainty = (anchor.get('accuracy_m') or 0) + (accuracy or 0)
            if displacement + uncertainty > cfg['radius_m']:
                finish('movement or accuracy uncertainty')
        cluster.append(point)
    finish('sample end; stop may continue')
    return stops, rejected


class Matcher:
    def __init__(self, catalogue):
        self.db = catalogue
        self.metadata = {r['key']: json.loads(r['value']) for r in
                         self.db.execute('SELECT key,value FROM metadata')}

    def nearby_town(self, lat, lon):
        for width in (0.1, 0.5, 2.0, 10.0, 180.0):
            if width == 180:
                # Stream the genuinely global fallback; do not load every town
                # or accidentally omit the other side of the antimeridian.
                rows = self.db.execute("SELECT * FROM places WHERE source='geonames'")
            else:
                lo, hi = lon-width, lon+width
                intervals = [(lo, hi)]
                if lo < -180:
                    intervals = [(lo+360, 180), (-180, hi)]
                elif hi > 180:
                    intervals = [(lo, 180), (-180, hi-360)]
                nearest_sql = ' OR '.join('(b.maxlon>=? AND b.minlon<=?)' for _ in intervals)
                params = [x for interval in intervals for x in interval] + [lat-width, lat+width]
                rows = self.db.execute('''SELECT * FROM places NOT INDEXED WHERE source='geonames'
                  AND id IN (SELECT b.id FROM bounds b WHERE (''' + nearest_sql + ''')
                  AND b.maxlat>=? AND b.minlat<=?)''', params)
            nearest = min(rows, key=lambda p: distance((lat, lon), (p['latitude'], p['longitude'])), default=None)
            if nearest is not None:
                separation = distance((lat, lon), (nearest['latitude'], nearest['longitude']))
                # Confirm the found neighbour cannot be beaten just outside
                # this search box (important at high latitudes).
                lower_bound = R * math.radians(width) * max(0, math.cos(math.radians(abs(lat)+width)))
                if separation <= lower_bound or width == 180:
                    return dict(name=nearest['name'], country_code=nearest['country'],
                        admin1=nearest['admin1'], geonames_id=nearest['object_id'],
                        distance_m=round(separation), label='nearest listed locality; not municipality/border proof')
        return None

    def candidates(self, lat, lon, radius_m=100, limit=5):
        if not (math.isfinite(lat) and math.isfinite(lon) and -85 <= lat <= 85 and -179 <= lon <= 179):
            return [], 'unsupported coordinate'
        bbox = self.metadata['osm_bbox']
        if not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            return [], 'outside pilot OSM catalogue; venues not searched'
        dy = math.degrees(radius_m/R)
        dx = dy/max(0.01, math.cos(math.radians(lat)))
        # Drive lookup from spatial IDs. NOT INDEXED still permits rowid/PK
        # probes, but prevents scanning the source-leading unique index for
        # every reference object before checking the spatial membership list.
        rows = self.db.execute('''SELECT * FROM places NOT INDEXED WHERE source='osm' AND category!='place'
            AND id IN (SELECT id FROM bounds WHERE maxlon>=? AND minlon<=?
            AND maxlat>=? AND minlat<=?)''', (lon-dx, lon+dx, lat-dy, lat+dy)).fetchall()
        result = []
        for row in rows:
            geometry = json.loads(row['geometry']) if row['geometry'] else None
            inside = bool(geometry and contains(geometry, lat, lon))
            d = 0 if inside else edge_distance(geometry, lat, lon) if geometry else distance(
                (lat, lon), (row['latitude'], row['longitude']))
            if d <= radius_m:
                result.append(dict(name=row['name'], name_en=row['name_en'],
                    osm_type=row['object_type'], osm_id=row['object_id'],
                    category=row['category'], subtype=row['subtype'], distance_m=round(d, 1),
                    spatial_evidence='inside mapped area' if inside else 'near mapped boundary' if geometry else 'near mapped point',
                    status='candidate only; present-day OSM is not historical attendance evidence'))
        result.sort(key=lambda p: (p['distance_m'], p['name'], p['osm_type'], p['osm_id']))
        return result[:limit], 'covered by pilot reference rectangle; OSM venue coverage may be incomplete'


def collect(history, gps_start, gps_end, google_start, google_end, bbox):
    history.execute('BEGIN')
    events = []
    for r in history.execute('''SELECT asset_id,locator,start_utc,end_utc,latitude,longitude,
        place_id,payload_json FROM events WHERE source=? AND kind=? AND start_utc<? AND end_utc>?
        AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? ORDER BY start_utc,locator''',
        ('google_timeline', 'visit', google_end, google_start,
         bbox[1], bbox[3], bbox[0], bbox[2])):
        p = json.loads(r['payload_json'])['visit']
        events.append(dict(source='google_timeline', kind='google_inferred_visit',
            start_utc=r['start_utc'], end_utc=r['end_utc'], latitude=r['latitude'], longitude=r['longitude'],
            asset_id=r['asset_id'], locator=r['locator'], google_place_id=r['place_id'],
            hierarchy_level=p.get('hierarchyLevel'), google_visit_probability=p.get('probability'),
            google_candidate_probability=p.get('topCandidate', {}).get('probability'),
            status='Google inference; not confirmed attendance'))
    points = [dict(r) for r in history.execute('''SELECT id,time_utc,latitude,longitude,accuracy_m
        FROM points WHERE source=? AND time_utc>=? AND time_utc<? ORDER BY time_utc,id LIMIT 50001''',
        ('gpslogger', gps_start, gps_end))]
    if len(points) > 50000:
        raise ValueError('pilot GPS sample exceeds 50000-point limit')
    history.rollback()  # Read snapshot only; never write the source index.
    return events, points, dict(google_timeline_visits=len(events), gpslogger_points=len(points))


def analyse(history_path, catalogue_path, gps_start, gps_end, google_start, google_end,
            google_timezone='UTC'):
    if timestamp(gps_end) <= timestamp(gps_start) or timestamp(gps_end)-timestamp(gps_start) > dt.timedelta(days=3):
        raise ValueError('GPS pilot window must be positive and no longer than three days')
    if timestamp(google_end) <= timestamp(google_start) or timestamp(google_end)-timestamp(google_start) > dt.timedelta(days=14):
        raise ValueError('Google pilot window must be positive and no longer than fourteen days')
    ZoneInfo(google_timezone)  # Validate before reading personal data.
    # Normalize to the history index's exact lexical UTC timestamp format.
    def utc(s):
        return timestamp(s).astimezone(dt.timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    with readonly(catalogue_path) as c:
        bbox = json.loads(c.execute("SELECT value FROM metadata WHERE key='osm_bbox'").fetchone()[0])
    with readonly(history_path) as h:
        events, points, snapshot = collect(h, utc(gps_start), utc(gps_end),
                                          utc(google_start), utc(google_end), bbox)
    stops, rejected = detect_stops(points)
    with readonly(catalogue_path) as c:
        matcher = Matcher(c)
        for item in events + stops:
            lat, lon = item['latitude'], item['longitude']
            item['nearby_locality'] = matcher.nearby_town(lat, lon)
            item['candidates'], item['coverage'] = matcher.candidates(lat, lon)
            item['display_timezone'] = google_timezone if item['source'] == 'google_timeline' else 'UTC'
        meta = matcher.metadata
    result = dict(schema_version=1, generated_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        pilot_snapshot_counts=snapshot, catalogue=meta, stop_settings=DEFAULTS,
        gps_window=[utc(gps_start), utc(gps_end)], google_window=[utc(google_start), utc(google_end)],
        gps_input_points=len(points), gps_rejected_points=rejected,
        gps_point_provenance='point IDs join observations.point_id to assets and original locators in history.sqlite',
        warnings=[
            'Private derived report. No verified attendance or destination totals.',
            'Nearby locality country codes are labels of the reference locality, not border containment.',
            'Current map names may differ from the historical visit date; missing venues remain unknown.',
            'Nested Google visits overlap; do not sum their durations or count them as separate trips.',
            'GPS spans describe sampled observations, not uninterrupted presence; gaps split candidates.',
            'GPS timestamps shown in UTC; Google visits use the requested display timezone, not literal export wall-clock strings.',
            'Venue reference covers only the selected pilot rectangle; worldwide nearby localities are available.',
            'No external assets, map tiles, tracking scripts, external API calls or confirmed-place overrides.'
        ], visits=events + stops)
    return result


def render(result):
    e = lambda s: html.escape(str(s), quote=True)
    groups = defaultdict(list)
    for visit in result['visits']:
        town = visit['nearby_locality']
        key = (town['country_code'] or 'Unknown', town['name']) if town else ('Unknown', 'Unknown locality')
        groups[key].append(visit)
    parts = ['''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Private travel pilot</title><style>
body{font:16px/1.5 system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;color:#20262b;background:#faf9f5}
h1,h2{line-height:1.2}article{border:1px solid #ccc;border-radius:8px;padding:1rem;margin:1rem 0;background:white}
small,.muted{color:#555}li{margin:.3rem 0}.warning{border-left:4px solid #b27618;padding-left:1rem}
code{overflow-wrap:anywhere}summary{cursor:pointer}nav a{margin-right:1rem}a{color:#285e74}
</style></head><body><h1>Google history & GPSLogger · private pilot</h1>
<p class="warning">Place names below are candidates, not confirmed visits. Country/city grouping uses the nearest listed locality, not administrative boundaries.</p>''']
    parts.append('<p>Generated ' + e(result['generated_utc']) + '. GPS sample: ' + e(result['gps_input_points']) +
                 ' points; inferred stops: ' + e(sum(v['source']=='gpslogger' for v in result['visits'])) + '.</p>')
    parts.append('<details><summary>Method, privacy and limitations</summary><ul>' +
                 ''.join('<li>'+e(w)+'</li>' for w in result['warnings']) + '</ul><p>Stop settings: <code>' +
                 e(json.dumps(result['stop_settings'], sort_keys=True)) + '</code></p></details>')
    parts.append('<nav>' + ''.join('<a href="#g'+str(i)+'">'+e(c)+' / '+e(t)+'</a>'
                 for i, (c, t) in enumerate(sorted(groups))) + '</nav>')
    for i, key in enumerate(sorted(groups)):
        parts.append('<h2 id="g'+str(i)+'">'+e(key[0])+' / near '+e(key[1])+'</h2>')
        for v in sorted(groups[key], key=lambda x: (x['start_utc'], x.get('locator', ''))):
            z = ZoneInfo(v['display_timezone'])
            fmt = lambda t: timestamp(t).astimezone(z).strftime('%d %b %Y %H:%M %Z')
            parts.append('<article><h3>'+e(fmt(v['start_utc']))+' → '+e(fmt(v['end_utc']))+'</h3>')
            parts.append('<p>'+e(v['status'])+' · '+e(v['coverage'])+'</p>')
            if v['candidates']:
                parts.append('<ul>')
                for p in v['candidates']:
                    parts.append('<li><strong>'+e(p['name'])+'</strong> — '+e(p['category'])+'/'+e(p['subtype'])+
                        '; '+e(p['spatial_evidence'])+', '+e(p['distance_m'])+' m. <small>OSM '+e(p['osm_type'])+' '+e(p['osm_id'])+'</small></li>')
                parts.append('</ul>')
            else:
                parts.append('<p><strong>Venue unknown.</strong> No place name inferred from absence.</p>')
            parts.append('<details><summary>Source evidence</summary><pre><code>'+e(json.dumps(
                {k: val for k, val in v.items() if k != 'candidates'}, indent=2, ensure_ascii=False))+'</code></pre></details></article>')
    parts.append('<footer><p>'+e(result['catalogue']['attribution'])+'</p><p>Reference hashes: <code>'+e(
        result['catalogue']['osm_sha256'])+'</code> / <code>'+e(result['catalogue']['geonames_sha256'])+
        '</code></p><p>'+e(result['catalogue']['limitations'])+'</p></footer></body></html>')
    return ''.join(parts)


def write_report(result, output):
    os.umask(0o077)
    output = Path(output)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError('choose a new report directory; previous pilot reports are preserved')
    with tempfile.TemporaryDirectory(prefix='.report-', dir=output.parent) as tmp:
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        (Path(tmp)/'report.json').write_text(payload, encoding='utf-8')
        (Path(tmp)/'report.html').write_text(render(result), encoding='utf-8')
        manifest = dict(json_sha256=hashlib.sha256(payload.encode()).hexdigest(),
                        html_sha256=hashlib.sha256((Path(tmp)/'report.html').read_bytes()).hexdigest())
        (Path(tmp)/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        os.rename(tmp, output)
    summary = dict(google_inferred_visits=sum(v['source']=='google_timeline' for v in result['visits']),
        gps_points=result['gps_input_points'], gps_inferred_stops=sum(v['source']=='gpslogger' for v in result['visits']),
        visits_with_osm_candidates=sum(bool(v['candidates']) for v in result['visits']))
    try:
        print(json.dumps(summary), flush=True)
    except BrokenPipeError:
        fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(fd, sys.stdout.fileno())
        os.close(fd)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history', required=True)
    p.add_argument('--catalogue', required=True)
    p.add_argument('--gps-start', required=True)
    p.add_argument('--gps-end', required=True)
    p.add_argument('--google-start', required=True)
    p.add_argument('--google-end', required=True)
    p.add_argument('--google-timezone', default='UTC')
    p.add_argument('--output', required=True)
    args = p.parse_args()
    write_report(analyse(args.history, args.catalogue, args.gps_start, args.gps_end,
                         args.google_start, args.google_end, args.google_timezone), args.output)
