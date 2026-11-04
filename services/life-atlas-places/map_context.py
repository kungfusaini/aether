#!/usr/bin/env python3
"""Build bounded local map context from public PBFs; no network or source writes."""
import argparse
import json
import math
import os
from pathlib import Path
import tempfile
from catalogue import sha256

MAJOR = {'motorway','trunk','primary','secondary','tertiary',
         'motorway_link','trunk_link','primary_link','secondary_link','tertiary_link'}
LOCAL = MAJOR | {'residential','unclassified','living_street','pedestrian','service','footway','path'}


def context_bounds(visits, margin_m=1500):
    if not visits:
        return None
    lat = sum(v['latitude'] for v in visits)/len(visits)
    dx = margin_m/(111195*max(.1, math.cos(math.radians(lat))))
    dy = margin_m/111195
    return [min(v['longitude'] for v in visits)-dx, min(v['latitude'] for v in visits)-dy,
            max(v['longitude'] for v in visits)+dx, max(v['latitude'] for v in visits)+dy]


def intersects(xy, bbox):
    return bool(xy and max(p[0] for p in xy)>=bbox[0] and min(p[0] for p in xy)<=bbox[2]
                and max(p[1] for p in xy)>=bbox[1] and min(p[1] for p in xy)<=bbox[3])


def extract(path, bbox, scratch, detailed=False, point_cap=300000):
    os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
    os.environ.setdefault('OMP_NUM_THREADS','1')
    import osmium
    from shapely.affinity import scale
    from shapely.geometry import LineString
    factory = osmium.geom.GeoJSONFactory()
    sx=111195*max(.1,math.cos(math.radians((bbox[1]+bbox[3])/2)))
    tolerance=3 if detailed else 35  # Map background only, never trajectory data.
    features = []
    count = 0
    skipped = 0

    def add(geometry, role, name='', subtype=''):
        nonlocal count
        if geometry['type']=='Point':
            count += 1
        else:
            count += len(geometry['coordinates'])
        if count > point_cap:
            raise ValueError('map context coordinate budget exceeded; choose a smaller window')
        features.append(dict(type='Feature', properties=dict(role=role,name=name,subtype=subtype), geometry=geometry))

    class Handler(osmium.SimpleHandler):
        def node(self, n):
            if n.tags.get('place') not in ('city','town','village','suburb','neighbourhood') or not n.tags.get('name'):
                return
            if n.location.valid() and bbox[0]<=n.location.lon<=bbox[2] and bbox[1]<=n.location.lat<=bbox[3]:
                add(dict(type='Point',coordinates=[n.location.lon,n.location.lat]),'town',n.tags['name'],n.tags['place'])

        def way(self, w):
            nonlocal skipped
            highway=w.tags.get('highway')
            if highway in (LOCAL if detailed else MAJOR):
                role='road'
            elif w.tags.get('natural')=='coastline':
                role='coast'
            elif w.tags.get('waterway') in ('river','canal'):
                role='water'
            else:
                return
            try:
                geometry=json.loads(factory.create_linestring(w))
            except (RuntimeError, ValueError):
                skipped += 1
                return
            if intersects(geometry['coordinates'],bbox):
                metric=scale(LineString(geometry['coordinates']),xfact=sx,yfact=111195,origin=(0,0))
                reduced=metric.simplify(tolerance,preserve_topology=False)
                geographic=scale(reduced,xfact=1/sx,yfact=1/111195,origin=(0,0))
                geometry['coordinates']=[[round(x,5),round(y,5)] for x,y in geographic.coords]
                add(geometry,role,w.tags.get('name',''),highway or '')

    Handler().apply_file(str(path),locations=True,idx='sparse_file_array,'+str(scratch))
    return dict(type='FeatureCollection',features=features,selection_bbox=bbox,
                osm_sha256=sha256(path),skipped_geometry=skipped,simplification_m=tolerance)


def build(report_path, history_map, gps_map, output):
    os.umask(0o077)
    report=json.loads(Path(report_path).read_text())
    output=Path(output)
    if output.exists():
        raise FileExistsError('preserve old context; select a new output')
    output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    result={}
    with tempfile.TemporaryDirectory(prefix='.map-context-',dir=output.parent) as tmp:
        for source,path,detailed in [('google_timeline',history_map,False),('gpslogger',gps_map,True)]:
            bbox=context_bounds([v for v in report['visits'] if v['source']==source])
            if bbox:
                result[source]=extract(path,bbox,Path(tmp)/source,detailed=detailed)
        result['attribution']='© OpenStreetMap contributors, ODbL 1.0. Context only, not navigation or observed routes.'
        pending=Path(tmp)/'context.json'
        pending.write_text(json.dumps(result,separators=(',',':'),ensure_ascii=False))
        os.replace(pending,output)
    print(json.dumps({s:len(v['features']) for s,v in result.items() if isinstance(v,dict)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',required=True)
    p.add_argument('--history-map',required=True)
    p.add_argument('--gps-map',required=True)
    p.add_argument('--output',required=True)
    args=p.parse_args()
    build(args.report,args.history_map,args.gps_map,args.output)
