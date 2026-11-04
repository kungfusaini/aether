from contextlib import closing
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from catalogue import SCHEMA, add_place, build, import_geonames, import_osm
from setup_reference import download
from places import (Matcher, analyse, collect, contains, detect_stops, distance, edge_distance,
                    readonly, render, write_report)


def point(i, minutes, lat=37.5, lon=15.0, accuracy=3):
    stamp = dt.datetime(2020, 7, 2, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=minutes)
    return dict(id=i, time_utc=stamp.isoformat(), latitude=lat, longitude=lon, accuracy_m=accuracy)


def square(x=15, y=37.5, size=.001):
    return {'type': 'MultiPolygon', 'coordinates': [[[[x,y], [x+size,y],
            [x+size,y+size], [x,y+size], [x,y]]]]}


class DownloadTest(unittest.TestCase):
    def response(self, payload, url='https://example.invalid/public'):
        result=io.BytesIO(payload)
        result.url=url
        return result

    def test_verified_public_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'public'
            with patch('urllib.request.urlopen',return_value=self.response(b'public')):
                receipt=download('https://example.invalid/public',path,100,hashlib.sha256(b'public').hexdigest())
            self.assertEqual(path.read_bytes(),b'public')
            self.assertEqual(receipt['bytes'],6)

    def test_hash_and_size_failures_preserve_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'public'
            path.write_bytes(b'old')
            for cap,expected in [(3,None),(100,'bad')]:
                with patch('urllib.request.urlopen',return_value=self.response(b'public')):
                    with self.assertRaises(ValueError):
                        download('https://example.invalid/public',path,cap,expected)
                self.assertEqual(path.read_bytes(),b'old')
                self.assertEqual(list(Path(tmp).iterdir()),[path])

    def test_http_redirect_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('urllib.request.urlopen',return_value=self.response(b'x','http://example.invalid')):
                with self.assertRaises(ValueError):
                    download('https://example.invalid',Path(tmp)/'out',10)


class StopsTest(unittest.TestCase):
    def test_stationary(self):
        stops, rejected = detect_stops([point(i,i*5) for i in range(4)])
        self.assertEqual(rejected, 0)
        self.assertEqual(stops[0]['point_ids'], [0,1,2,3])
        self.assertEqual(stops[0]['observed_span_seconds'], 900)
        self.assertIn('may continue', stops[0]['ends_because'])

    def test_gap_splits(self):
        stops, _ = detect_stops([point(i,t) for i,t in enumerate([0,5,10,50,55,60])])
        self.assertEqual(len(stops), 2)
        self.assertEqual(stops[0]['ends_because'], 'observation gap')

    def test_movement_not_stop(self):
        stops, _ = detect_stops([point(i, i*5, lon=15+i*.002) for i in range(8)])
        self.assertFalse(stops)

    def test_short_dwell_not_stop(self):
        self.assertFalse(detect_stops([point(i,i) for i in range(5)])[0])

    def test_unknown_accuracy_explicit(self):
        s,_ = detect_stops([point(i,i*5,accuracy=None) for i in range(3)])
        self.assertEqual(s[0]['missing_accuracy_points'],3)
        self.assertIsNone(s[0]['max_accuracy_m'])

    def test_bad_accuracy_breaks(self):
        points = [point(i,i*5) for i in range(5)]
        points[2]['accuracy_m'] = 500
        stops, rejected = detect_stops(points)
        self.assertEqual(rejected, 1)
        self.assertFalse(stops)

    def test_moderate_uncertainty_not_overconfident(self):
        self.assertFalse(detect_stops([point(i,i*5,accuracy=50) for i in range(5)])[0])

    def test_nan_and_polar_rejected(self):
        for lat in [float('nan'), 89]:
            self.assertEqual(detect_stops([point(0,0,lat=lat)])[1],1)

    def test_bad_parameters_and_order(self):
        with self.assertRaises(ValueError):
            detect_stops([], radius_m=0)
        with self.assertRaises(ValueError):
            detect_stops([point(0,5),point(1,0)])


class GeometryTest(unittest.TestCase):
    def test_haversine(self):
        self.assertLess(abs(distance((0,0),(0,1))-111195),2)

    def test_inside_hole_and_outside(self):
        g=square()
        self.assertTrue(contains(g,37.5005,15.0005))
        self.assertFalse(contains(g,37.502,15.002))
        g['coordinates'][0].append([[15.0002,37.5002],[15.0008,37.5002],
            [15.0008,37.5008],[15.0002,37.5008],[15.0002,37.5002]])
        self.assertFalse(contains(g,37.5005,15.0005))
        self.assertGreater(edge_distance(g,37.5005,15.0005),20)

    def test_boundary_distance(self):
        self.assertLess(edge_distance(square(),37.5005,15.0011),10)
        self.assertEqual(edge_distance(square(),37.5,15),0)


class CatalogueTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.path=self.root/'catalogue.sqlite'
        self.db=sqlite3.connect(self.path)
        self.db.row_factory=sqlite3.Row
        self.db.executescript(SCHEMA)
        metadata={'osm_bbox':[12,35,16,39], 'attribution':'OSM & GeoNames',
                  'limitations':'synthetic', 'osm_sha256':'abc','geonames_sha256':'def'}
        self.db.executemany('INSERT INTO metadata VALUES (?,?)',[(k,json.dumps(v)) for k,v in metadata.items()])
        add_place(self.db,'geonames','populated_place',1,'Town',37.5,15,'locality',country='IT')
        add_place(self.db,'osm','node',2,'<script>alert(1)</script>',37.5005,15.0005,'amenity','cafe')
        add_place(self.db,'osm','way',3,'Hotel',37.5005,15.0005,'tourism','hotel',geometry=square())
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_match_inside_and_near(self):
        hits,coverage=Matcher(self.db).candidates(37.5005,15.0005)
        self.assertEqual(len(hits),2)
        self.assertIn('covered',coverage)
        self.assertEqual(next(x for x in hits if x['name']=='Hotel')['spatial_evidence'],'inside mapped area')
        self.assertEqual(Matcher(self.db).nearby_town(37.5,15)['country_code'],'IT')

    def test_spatial_queries_probe_only_selected_reference_ids(self):
        queries=[]
        self.db.set_trace_callback(queries.append)
        matcher=Matcher(self.db)
        matcher.nearby_town(37.5,15)
        matcher.candidates(37.5005,15.0005)
        self.db.set_trace_callback(None)
        spatial=[q for q in queries if 'FROM bounds' in q]
        self.assertGreaterEqual(len(spatial),2)
        for query in spatial:
            plan=[r[3] for r in self.db.execute('EXPLAIN QUERY PLAN '+query)]
            self.assertTrue(any('SEARCH places USING INTEGER PRIMARY KEY' in line for line in plan),plan)

    def test_outside_coverage_not_missing_visit(self):
        hits,coverage=Matcher(self.db).candidates(51.5,-.1)
        self.assertFalse(hits)
        self.assertIn('outside',coverage)

    def test_nearest_town_wraps_longitude(self):
        add_place(self.db,'geonames','populated_place',90,'Across date line',0,-179.95,'locality',country='FJ')
        add_place(self.db,'geonames','populated_place',91,'Farther same side',0,179.2,'locality',country='FJ')
        self.assertEqual(Matcher(self.db).nearby_town(0,179.95)['name'],'Across date line')

    def test_global_fallback_really_global(self):
        add_place(self.db,'geonames','populated_place',92,'Far north',87,-179,'locality',country='XX')
        self.assertEqual(Matcher(self.db).nearby_town(89,179)['name'],'Far north')

    def test_town_category_excluded(self):
        add_place(self.db,'osm','node',4,'Village',37.5005,15.0005,'place','village')
        self.assertNotIn('Village',[p['name'] for p in Matcher(self.db).candidates(37.5005,15.0005)[0]])

    def test_readonly(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        with readonly(self.path) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM places')
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_geonames_zip(self):
        path=self.root/'towns.zip'
        row=['99','Other','Other','','38','16','P','PPL','IT','','15','','','', '1000','','','Europe/Rome','2026-01-01']
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('cities1000.txt','\t'.join(row)+'\n')
        import_geonames(self.db,path)
        self.assertEqual(self.db.execute("SELECT count(*) FROM places WHERE source='geonames'").fetchone()[0],2)

    def test_catalogue_reuse_and_closed_output_pipe(self):
        osm=self.root/'region.osm.pbf'
        osm.write_bytes(b'public synthetic reference')
        towns=self.root/'towns.zip'
        row=['99','Other','Other','','38','16','P','PPL','IT','','15','','','', '1000','','','Europe/Rome','2020-01-01']
        with zipfile.ZipFile(towns,'w') as z:
            z.writestr('cities1000.txt','\t'.join(row)+'\n')
        out=self.root/'built.sqlite'
        url='https://download.geofabrik.de/synthetic.osm.pbf'
        bbox=[12,35,16,39]
        with patch('catalogue.import_osm',return_value=0) as parse:
            self.assertFalse(build(osm,towns,out,bbox,url)['reused'])
            before=out.read_bytes()
            self.assertTrue(build(osm,towns,out,bbox,url)['reused'])
            self.assertEqual(out.read_bytes(),before)
            parse.assert_called_once()
        command=[sys.executable,'-B',str(Path(__file__).with_name('catalogue.py')),
            '--osm',str(osm),'--towns',str(towns),'--output',str(out),'--bbox',*map(str,bbox),'--osm-url',url]
        with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as proc:
            proc.stdout.close()
            error=proc.stderr.read()
            self.assertEqual(proc.wait(timeout=10),0,error)
        osm.write_bytes(b'changed public reference')
        with self.assertRaises(ValueError):
            build(osm,towns,out,bbox,url)
        self.assertEqual(out.read_bytes(),before)

    def create_history(self):
        path=self.root/'history.sqlite'
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('''CREATE TABLE points(id INTEGER PRIMARY KEY,source TEXT,time_utc TEXT,
                latitude REAL,longitude REAL,accuracy_m REAL);
                CREATE TABLE events(asset_id INTEGER,locator TEXT,source TEXT,kind TEXT,
                start_utc TEXT,end_utc TEXT,latitude REAL,longitude REAL,place_id TEXT,payload_json TEXT);''')
            db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)',
                (1,'semanticSegments/1','google_timeline','visit','2020-07-01T10:00:00.000000Z',
                 '2020-07-01T11:00:00.000000Z',37.5005,15.0005,'opaque',json.dumps({'visit':{'hierarchyLevel':1}})))
            for i in range(4):
                p=point(i,i*5,lat=37.5005,lon=15.0005)
                # Stored source uses normalized microsecond UTC, as the importer does.
                t=dt.datetime.fromisoformat(p['time_utc']).isoformat(timespec='microseconds').replace('+00:00','Z')
                db.execute('INSERT INTO points VALUES (?,?,?,?,?,?)',(i,'gpslogger',t,p['latitude'],p['longitude'],3))
        return path

    def test_shared_pipeline_and_report(self):
        history=self.create_history()
        before=history.read_bytes()
        r=analyse(history,self.path,'2020-07-02T00:00:00Z','2020-07-03T00:00:00Z',
                  '2020-07-01T00:00:00Z','2020-07-02T00:00:00Z','Europe/Rome')
        self.assertEqual({v['source'] for v in r['visits']},{'google_timeline','gpslogger'})
        self.assertTrue(all(v['candidates'] for v in r['visits']))
        self.assertEqual(r['visits'][0]['hierarchy_level'],1)
        self.assertEqual(history.read_bytes(),before)
        doc=render(r)
        self.assertIn('&lt;script&gt;',doc)
        self.assertNotIn('<script>',doc)
        self.assertNotIn('<img',doc)
        self.assertIn('Content-Security-Policy',doc)
        self.assertIn('12:00 CEST',doc)
        out=self.root/'report'
        write_report(r,out)
        self.assertEqual((out/'report.html').stat().st_mode & 0o777,0o600)
        self.assertEqual(out.stat().st_mode & 0o777,0o700)
        self.assertEqual(json.loads((out/'report.json').read_text()),r)
        with self.assertRaises(FileExistsError):
            write_report(r,out)

    def test_report_closed_output_pipe(self):
        history=self.create_history()
        out=self.root/'pipe-report'
        command=[sys.executable,'-B',str(Path(__file__).with_name('places.py')),
            '--history',str(history),'--catalogue',str(self.path),
            '--gps-start','2020-07-02T00:00:00Z','--gps-end','2020-07-03T00:00:00Z',
            '--google-start','2020-07-01T00:00:00Z','--google-end','2020-07-02T00:00:00Z',
            '--output',str(out)]
        with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as proc:
            proc.stdout.close()
            error=proc.stderr.read()
            self.assertEqual(proc.wait(timeout=10),0,error)
        self.assertEqual(len(json.loads((out/'report.json').read_text())['visits']),2)
        self.assertTrue((out/'manifest.json').exists())

    def test_multiple_catalogues_cover_gps_without_changing_history_region(self):
        history=self.create_history()
        with closing(sqlite3.connect(history)) as db,db:
            db.execute("UPDATE points SET latitude=51.5,longitude=-.5 WHERE source='gpslogger'")
        before=history.read_bytes()
        add_place(self.db,'geonames','populated_place',98,'Other town',51.5,-.5,'locality',country='YY')
        self.db.commit()
        other=self.root/'other.sqlite'
        with closing(sqlite3.connect(other)) as db,db:
            db.executescript(SCHEMA)
            db.executemany('INSERT INTO metadata VALUES (?,?)',[(k,json.dumps(v)) for k,v in
                {'osm_bbox':[-1,51,0,52],'osm_sha256':'other-hash','attribution':'synthetic'}.items()])
            add_place(db,'osm','node',7,'Library',51.5,-.5,'amenity','library')
        r=analyse(history,[self.path,other],'2020-07-02T00:00:00Z','2020-07-03T00:00:00Z',
                  '2020-07-01T00:00:00Z','2020-07-02T00:00:00Z')
        gps=next(v for v in r['visits'] if v['source']=='gpslogger')
        self.assertEqual(gps['candidates'][0]['name'],'Library')
        self.assertEqual(gps['catalogue_reference']['osm_sha256'],'other-hash')
        self.assertEqual(gps['nearby_locality']['name'],'Other town')
        self.assertEqual(len(r['catalogues']),2)
        self.assertEqual(history.read_bytes(),before)

    def test_collection_does_not_inventory_whole_history(self):
        history=self.create_history()
        queries=[]
        with readonly(history) as db:
            db.set_trace_callback(queries.append)
            _,points,counts=collect(db,'2020-07-02T00:00:00.000000Z','2020-07-03T00:00:00.000000Z',
                '2020-07-01T00:00:00.000000Z','2020-07-02T00:00:00.000000Z',[12,35,16,39])
        self.assertEqual(counts,{'google_timeline_visits':1,'gpslogger_points':len(points)})
        self.assertFalse(any('GROUP BY' in sql.upper() for sql in queries))

    def test_gps_window_bounded(self):
        h=self.create_history()
        with self.assertRaises(ValueError):
            analyse(h,self.path,'2020-07-02T00:00:00Z','2020-07-10T00:00:00Z',
                    '2020-07-01T00:00:00Z','2020-07-02T00:00:00Z')

    def test_google_window_bounded(self):
        h=self.create_history()
        with self.assertRaises(ValueError):
            analyse(h,self.path,'2020-07-02T00:00:00Z','2020-07-03T00:00:00Z',
                    '2020-07-01T00:00:00Z','2020-08-01T00:00:00Z')

    @unittest.skipUnless(importlib.util.find_spec('osmium'), 'isolated parser not installed locally')
    def test_real_parser_node_and_area(self):
        path=self.root/'test.osm'
        path.write_text('''<osm version="0.6">
          <node id="1" lat="37.5" lon="15"><tag k="name" v="Cafe"/><tag k="amenity" v="cafe"/></node>
          <node id="2" lat="37.5" lon="15.001"/>
          <node id="3" lat="37.501" lon="15.001"/>
          <node id="4" lat="37.501" lon="15"/>
          <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>
            <tag k="name" v="Test hotel"/><tag k="tourism" v="hotel"/><tag k="building" v="yes"/></way>
        </osm>''')
        self.assertEqual(import_osm(self.db,path,self.root/'index',[12,35,16,39]),0)
        rows=self.db.execute("SELECT object_type,object_id FROM places WHERE name='Test hotel'").fetchall()
        self.assertEqual([tuple(r) for r in rows],[('way','10')])
        # The area overlaps this tiny selection, but its centre and every
        # named node are outside. Selection must retain its containment shape.
        with closing(sqlite3.connect(self.root/'edge.sqlite')) as edge:
            edge.executescript(SCHEMA)
            import_osm(edge,path,self.root/'edge-index',[15.0009,37.5009,15.0011,37.5011])
            self.assertEqual(edge.execute("SELECT count(*) FROM places WHERE name='Test hotel'").fetchone()[0],1)
            self.assertEqual(edge.execute("SELECT count(*) FROM places WHERE object_type='node'").fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
