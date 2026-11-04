from contextlib import closing
import copy
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from map_context import context_bounds, extract, intersects
from map_report import group_records, line_distance, prepare, record_days, render_map, safe_json


def synthetic_report(dense=False):
    def record(source,start,end,lat,lon,pid=None):
        return dict(source=source,kind='inferred_stop' if source=='gpslogger' else 'google_inferred_visit',
            start_utc=start,end_utc=end,latitude=lat,longitude=lon,google_place_id=pid,
            display_timezone='UTC',nearby_locality=dict(name='Example Town',country_code='XX'),
            candidates=[dict(name='Example Castle',osm_type='way',osm_id='1',category='historic',
                            subtype='castle',distance_m=0,spatial_evidence='inside mapped area')],
            status='candidate only',hierarchy_level=0)
    visits=[record('google_timeline','2020-07-01T10:00:00Z','2020-07-01T11:00:00Z',37.5,15,'one'),
            record('google_timeline','2020-07-02T10:00:00Z','2020-07-02T11:00:00Z',37.5,15,'one'),
            record('google_timeline','2020-07-01T13:00:00Z','2020-07-01T14:00:00Z',37.51,15.01,'two'),
            record('gpslogger','2020-07-03T10:00:00Z','2020-07-03T10:15:00Z',37.5,15),
            record('gpslogger','2020-07-03T12:00:00Z','2020-07-03T12:15:00Z',37.5001,15),
            record('gpslogger','2020-07-03T13:00:00Z','2020-07-03T13:15:00Z',37.51,15.01)]
    if dense:
        visits.append(record('google_timeline','2020-07-01T10:30:00Z','2020-07-01T11:30:00Z',37.5,15,'nearby-but-distinct'))
    context={s:dict(type='FeatureCollection',features=[dict(type='Feature',
        properties=dict(role='road',name='Example Road',subtype='residential'),
        geometry=dict(type='LineString',coordinates=[[14.999,37.5],[15.002,37.5]]))],
        osm_sha256='public-hash',selection_bbox=[14.9,37.4,15.1,37.6]) for s in ('google_timeline','gpslogger')}
    r=dict(visits=visits,google_window=['2020-07-01T00:00:00Z','2020-07-03T00:00:00Z'],
        gps_window=['2020-07-03T00:00:00Z','2020-07-04T00:00:00Z'],warnings=['Synthetic data only'],
        catalogue=dict(osm_sha256='public-hash'),catalogues=[dict(osm_sha256='public-hash')],
        map_context=context)
    r['view_groups']=group_records(r,context)
    for v in visits:v['display_days']=record_days(v,r)
    r['map_assets']=dict(leaflet_js='/* local test library */',leaflet_css='',cluster_js='',cluster_css='',
        land=dict(type='FeatureCollection',features=[]),leaflet_license='BSD fixture',cluster_license='MIT fixture')
    return r


class GroupingTest(unittest.TestCase):
    def test_repeated_records_group_without_losing_times(self):
        r=synthetic_report();groups=r['view_groups']
        self.assertEqual([g['record_count'] for g in groups],[2,1,2,1])
        self.assertEqual(len(r['visits']),6)
        self.assertIn('gaps',groups[2]['grouping_note'])
        self.assertEqual(groups[2]['title'],'Recorded area near Example Road')
        self.assertEqual(groups[2]['street_reference']['context_feature_index'],0)
        self.assertEqual(groups[2]['street_reference']['osm_sha256'],'public-hash')
        self.assertEqual(groups[0]['days'],['2020-07-01','2020-07-02'])

    def test_distinct_google_ids_not_merged_for_proximity(self):
        r=synthetic_report();r['visits'][2]['latitude']=37.5;r['visits'][2]['longitude']=15
        self.assertEqual(len([g for g in group_records(r,r['map_context']) if g['source']=='google_timeline']),2)

    def test_complete_link_does_not_chain_distant_gps_centres(self):
        r=synthetic_report();gps=[v for v in r['visits'] if v['source']=='gpslogger']
        for i,v in enumerate(gps):v['latitude']=37.5+i*.0007;v['longitude']=15
        groups=[g for g in group_records(r,r['map_context']) if g['source']=='gpslogger']
        self.assertEqual([g['record_count'] for g in groups],[2,1])

    def test_unknown_google_id_does_not_merge(self):
        r=synthetic_report()
        for v in r['visits']:v['google_place_id']=None
        self.assertEqual(len([g for g in group_records(r,r['map_context']) if g['source']=='google_timeline']),3)

    def test_long_stay_not_assigned_to_nearby_school(self):
        r=synthetic_report();v=r['visits'][0];v['end_utc']='2020-07-01T23:00:00Z'
        v['candidates']=[dict(name='School',osm_type='way',osm_id='2',category='amenity',subtype='school',distance_m=35,spatial_evidence='near mapped boundary')]
        r['visits']=r['visits'][:1]
        g=group_records(r,r['map_context'])[0]
        self.assertTrue(g['title'].startswith('Unresolved stay near'))
        self.assertNotIn('School',g['title'])

    def test_broad_land_cover_does_not_hide_specific_place_name(self):
        r=synthetic_report();r['visits']=r['visits'][:1]
        r['visits'][0]['candidates']=[dict(name='Regional lava field',osm_type='way',osm_id='10',category='natural',subtype='bare_rock',distance_m=0,spatial_evidence='inside mapped area'),
            dict(name='Named refuge',osm_type='way',osm_id='11',category='tourism',subtype='hotel',distance_m=0,spatial_evidence='inside mapped area')]
        g=group_records(r,r['map_context'])[0]
        self.assertEqual(g['title'],'Near Named refuge')
        self.assertEqual(len(g['candidates']),2)

    def test_day_span_clipped_and_midnight_exclusive(self):
        r=synthetic_report();v=r['visits'][0]
        v['start_utc']='2020-06-01T00:00:00Z';v['end_utc']='2020-07-02T00:00:00Z'
        self.assertEqual(record_days(v,r),['2020-07-01'])

    def test_line_is_not_closed(self):
        self.assertGreater(line_distance(.3,.3,[[0,0],[1,0],[1,1]]),30000)

    def test_bounds_contains_context_margin(self):
        bbox=context_bounds(synthetic_report()['visits'])
        self.assertLess(bbox[0],15);self.assertGreater(bbox[2],15.01)
        self.assertTrue(intersects([[0,1],[2,1]],[.5,.5,1.5,1.5]))
        self.assertIsNone(context_bounds([]))


class RenderingTest(unittest.TestCase):
    def test_safe_json_and_hash_csp(self):
        r=synthetic_report();r['view_groups'][0]['title']='</script><script>evil()</script>&'
        doc=render_map(r)
        self.assertNotIn('</script><script>evil()',doc)
        self.assertIn('\\u003c/script',doc)
        self.assertIn('Your travel map',doc)
        self.assertIn('id="map"',doc)
        self.assertIn("connect-src &#x27;none&#x27;",doc)
        class Inspect(HTMLParser):
            def __init__(self):super().__init__();self.scripts=[];self.current=None;self.policy=''
            def handle_starttag(self,t,a):
                if t=='script':self.current=[]
                if t=='meta' and dict(a).get('http-equiv')=='Content-Security-Policy':self.policy=dict(a)['content']
            def handle_data(self,d):
                if self.current is not None:self.current.append(d)
            def handle_endtag(self,t):
                if t=='script':self.scripts.append(''.join(self.current));self.current=None
        parsed=Inspect();parsed.feed(doc)
        self.assertEqual(len(parsed.scripts),3)
        import base64
        for s in parsed.scripts:
            h=base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()
            self.assertIn('sha256-'+h,parsed.policy)
        self.assertNotIn('unsafe-eval',parsed.policy)

    def test_incompatible_context_rejected_before_assets(self):
        r=synthetic_report()
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'context.json';c=copy.deepcopy(r['map_context'])
            c['gpslogger']['osm_sha256']='wrong';p.write_text(json.dumps(c))
            with self.assertRaisesRegex(ValueError,'provenance'):
                prepare(r,tmp,p)

    def test_context_hash_must_match_the_source_region(self):
        r=synthetic_report();r['catalogues'].append(dict(osm_sha256='other-region'))
        for v in r['visits']:
            if v['source']=='gpslogger':v['catalogue_reference']=dict(osm_sha256='other-region')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'context.json';p.write_text(json.dumps(r['map_context']))
            with self.assertRaisesRegex(ValueError,'provenance'):
                prepare(r,tmp,p)

    @unittest.skipUnless(importlib.util.find_spec('osmium'),'parser not installed locally')
    def test_actual_context_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'map.osm'
            p.write_text('''<osm version="0.6"><node id="1" lat="37.5" lon="15"><tag k="place" v="town"/><tag k="name" v="Example"/></node>
              <node id="2" lat="37.5" lon="15.01"/><way id="10"><nd ref="1"/><nd ref="2"/>
              <tag k="highway" v="residential"/><tag k="name" v="Example Road"/></way></osm>''')
            context=extract(p,[14.9,37.4,15.1,37.6],Path(tmp)/'idx',detailed=True)
            self.assertEqual({f['properties']['role'] for f in context['features']},{'road','town'})
            with self.assertRaises(ValueError):
                extract(p,[14.9,37.4,15.1,37.6],Path(tmp)/'idx2',detailed=True,point_cap=1)


if __name__=='__main__':unittest.main()
