"""Map-first, self-contained offline presentation. Source records remain intact."""
import base64
import datetime as dt
import hashlib
import html
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from places import distance, timestamp
from setup_map import LAND_SHA256

JS_SHA = 'db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a'
CSS_SHA = 'a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6'
CLUSTER_JS_SHA = '1e4e1d22972a3926f48598e0caf14e3fe7049835d428a344fed4f9e3665b3508'
CLUSTER_CSS_SHA = '614dea0a98ff3f4ead74f04918f6b1d1b9ba435c25b5fc23b21a394d1e3e4d87'
CLUSTER_DEFAULT_SHA = '61258232d98d64dc2a7b1e02130d67421bc5b9bda5994eef70228ff97570c170'


def line_distance(lat, lon, coordinates):
    sx=111195*math.cos(math.radians(lat));sy=111195
    best=float('inf')
    # A road is not a closed ring: never invent a last-to-first shortcut.
    for a,b in zip(coordinates,coordinates[1:]):
        ax,ay=(a[0]-lon)*sx,(a[1]-lat)*sy
        bx,by=(b[0]-lon)*sx,(b[1]-lat)*sy
        dx,dy=bx-ax,by-ay
        fraction=max(0,min(1,-(ax*dx+ay*dy)/(dx*dx+dy*dy))) if dx or dy else 0
        best=min(best,math.hypot(ax+fraction*dx,ay+fraction*dy))
    return best


def street_hint(lat, lon, context):
    nearby=[]
    for index,feature in enumerate(context.get('features',[])):
        p=feature['properties'];g=feature['geometry']
        if p.get('role')=='road' and p.get('name') and g['type']=='LineString':
            bbox=p.get('bounds')
            dx=100/(111195*max(.1,math.cos(math.radians(lat))));dy=100/111195
            if bbox and (bbox[2]<lon-dx or bbox[0]>lon+dx or bbox[3]<lat-dy or bbox[1]>lat+dy):
                continue
            d=line_distance(lat,lon,g['coordinates'])
            if d<=100:nearby.append((d,p['name'],index))
    if not nearby:return None
    d,name,index=min(nearby)
    return dict(name=name,distance_m=round(d,1),context_feature_index=index,
                osm_sha256=context.get('osm_sha256'),basis='near simplified map road; not proof of attendance')


def record_days(v, result):
    z=ZoneInfo(v['display_timezone'])
    lo,hi=result['google_window'] if v['source']=='google_timeline' else result['gps_window']
    start=max(timestamp(lo),timestamp(v['start_utc']))
    end=min(timestamp(hi),timestamp(v['end_utc']))
    if end<=start:return []
    a=start.astimezone(z).date();b=(end-dt.timedelta(microseconds=1)).astimezone(z).date()
    return [(a+dt.timedelta(days=i)).isoformat() for i in range((b-a).days+1)]


def title_candidate(candidates):
    # Cartographic specificity is a display preference, not a confidence score.
    # A regional land-cover polygon must not bury a named hotel/monument at
    # the same point. Keep every candidate and its original evidence in details.
    def broad(p):
        return ((p['category']=='natural' and p['subtype'] in ('bare_rock','wood','scrub','water','wetland','tree'))
                or (p['category']=='leisure' and p['subtype']=='nature_reserve')
                or (p['category']=='tourism' and p['subtype']=='information'))
    pool=[p for p in candidates if not broad(p)] or candidates
    rank={'historic':0,'tourism':1,'amenity':2,'leisure':3,'shop':4,'natural':5}
    return min(pool,key=lambda p:(p['distance_m'],rank.get(p['category'],6),p['name'])) if pool else None


def group_records(result, context, radius_m=100):
    if not 0<radius_m<=200:raise ValueError('bounded grouping radius required')
    groups=[]
    # Historical records first. Same source place ID, not proximity alone, is
    # used to avoid merging different neighbouring restaurants/attractions.
    for source in ('google_timeline','gpslogger'):
        for index,v in enumerate(result['visits']):
            if v['source']!=source:continue
            chosen=None
            for group in groups:
                if group['source']!=source:continue
                if source=='google_timeline':
                    if v.get('google_place_id') and group['source_place_id']==v['google_place_id']:
                        chosen=group;break
                elif all(distance((v['latitude'],v['longitude']),
                                  (result['visits'][j]['latitude'],result['visits'][j]['longitude']))<=radius_m
                         for j in group['record_indices']):
                    chosen=group;break
            if chosen is None:
                chosen=dict(id='place-'+str(len(groups)),source=source,
                            source_place_id=v.get('google_place_id'),record_indices=[])
                groups.append(chosen)
            chosen['record_indices'].append(index)
    for group in groups:
        records=[result['visits'][i] for i in group['record_indices']]
        group['latitude']=sum(v['latitude'] for v in records)/len(records)
        group['longitude']=sum(v['longitude'] for v in records)/len(records)
        group['days']=sorted({d for v in records for d in record_days(v,result)})
        group['record_count']=len(records)
        group['locality']=records[0].get('nearby_locality')
        group['street_reference']=street_hint(group['latitude'],group['longitude'],context.get(group['source'],{}))
        group['street']=group['street_reference']['name'] if group['street_reference'] else None
        unique={}
        for v in records:
            for p in v['candidates']:
                key=(p['osm_type'],p['osm_id'])
                if key not in unique or p['distance_m']<unique[key]['distance_m']:unique[key]=p
        candidates=sorted(unique.values(),key=lambda p:(p['distance_m'],p['name'],p['osm_type'],p['osm_id']))
        group['candidates']=candidates[:8]
        town=group['locality']['name'] if group['locality'] else 'an unnamed locality'
        near=group['street'] or town
        long_stay=any((timestamp(v['end_utc'])-timestamp(v['start_utc'])).total_seconds()>=4*3600 for v in records)
        strong=any(p['spatial_evidence']=='inside mapped area' for p in candidates)
        if group['source']=='gpslogger':
            group['title']='Recorded area near '+near
            group['title_basis']='Spatial group, not a confirmed venue or uninterrupted stay'
        elif long_stay and not strong:
            group['title']='Unresolved stay near '+near
            group['title_basis']='Nearby businesses are not evidence of accommodation'
        elif candidates:
            group['title']='Near '+title_candidate(candidates)['name']
            group['title_basis']='Candidate map match, not confirmed attendance'
        else:
            group['title']='Location near '+near
            group['title_basis']='Venue unresolved'
        group['grouping_note']=('Same Google place ID; overlapping/nested records retained' if group['source']=='google_timeline'
                                else f'All segment centres within {radius_m:g} m of each other; time gaps preserved')
    return groups


def checked(path, expected, cap):
    path=Path(path)
    if path.stat().st_size>cap:raise ValueError('map asset too large')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('map asset integrity mismatch')
    return data.decode('utf-8')


def prepare(result, reference_dir, context_path):
    root=Path(reference_dir)
    if Path(context_path).stat().st_size>12*1024*1024:raise ValueError('context budget exceeded')
    context=json.loads(Path(context_path).read_text())
    hashes={m['osm_sha256'] for m in result.get('catalogues',[result['catalogue']])}
    for source in ('google_timeline','gpslogger'):
        selected=[v for v in result['visits'] if v['source']==source]
        if not selected:continue
        expected={v['catalogue_reference']['osm_sha256'] for v in selected if v.get('catalogue_reference')}
        if (source not in context or context[source]['osm_sha256'] not in hashes
                or expected and expected!={context[source]['osm_sha256']}):
            raise ValueError('context/reference provenance mismatch')
        for feature in context[source]['features']:
            if feature['geometry']['type']=='LineString':
                xy=feature['geometry']['coordinates']
                feature['properties']['bounds']=[min(p[0] for p in xy),min(p[1] for p in xy),max(p[0] for p in xy),max(p[1] for p in xy)]
        bbox=context[source]['selection_bbox']
        if not all(bbox[0]<=v['longitude']<=bbox[2] and bbox[1]<=v['latitude']<=bbox[3] for v in selected):
            raise ValueError('context does not cover selected observations')
    enriched=dict(result)
    enriched['visits']=[dict(v,display_days=record_days(v,result)) for v in result['visits']]
    enriched['view_groups']=group_records(result,context)
    enriched['map_context']=context
    enriched['map_assets']=dict(
        leaflet_js=checked(root/'ui/leaflet.js',JS_SHA,200000),
        leaflet_css=checked(root/'ui/leaflet.css',CSS_SHA,30000),
        cluster_js=checked(root/'ui/markercluster.js',CLUSTER_JS_SHA,50000),
        cluster_css=checked(root/'ui/markercluster.css',CLUSTER_CSS_SHA,5000)+checked(root/'ui/markercluster-default.css',CLUSTER_DEFAULT_SHA,5000),
        cluster_license=(root/'ui/MARKERCLUSTER-LICENSE').read_text(),
        land=json.loads(checked(root/'land-50m.geojson',LAND_SHA256,3*1024*1024)),
        leaflet_license=(root/'ui/LEAFLET-LICENSE').read_text(),
        land_credit='Natural Earth 1:50m land, public domain; broad context, not precise coastlines')
    enriched['warnings']=list(result['warnings'])+[
        'Spatial groups are not destination counts. Time gaps and source records are retained.',
        'Background roads/coasts are map context, not observed paths; no route lines are inferred.',
        'Only selected regions have detailed local map data. Natural Earth is coarse background context.'
    ]
    return enriched


def safe_json(data):
    return json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e').replace('\u2028','\\u2028').replace('\u2029','\\u2029')


def render_map(result):
    root=Path(__file__).parent
    assets=result['map_assets']
    public={k:v for k,v in result.items() if k!='map_assets'}
    public['land']=assets['land']
    # Names are JSON-escaped here and assigned with textContent in the client.
    app='const REPORT='+safe_json(public)+';\n'+(root/'map_view.js').read_text()
    leaflet=assets['leaflet_js']
    cluster=assets['cluster_js']
    if any('</script' in s.lower() for s in (leaflet,cluster)):raise ValueError('unexpected library script terminator')
    scripts=[leaflet,cluster,app]
    hashes=['sha256-'+base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() for s in scripts]
    policy="default-src 'none'; script-src "+' '.join("'"+h+"'" for h in hashes)+"; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"
    css=assets['leaflet_css']+'\n'+assets['cluster_css']+'\n'+(root/'map_view.css').read_text()
    if '</style' in css.lower():raise ValueError('unexpected stylesheet terminator')
    e=lambda s:html.escape(str(s),quote=True)
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="'''+e(policy)+'''">
<title>Private travel map</title><style>'''+css+'''</style></head><body>
<header><p class="eyebrow">PRIVATE · LOCAL MAP DATA ONLY</p><h1>Your travel map</h1>
<p class="intro">Explore recorded areas and possible place matches—not a list of confirmed visits.</p>
<div class="toolbar"><div class="sources" role="group" aria-label="Data source">
<button id="history" type="button">Trip history</button><button id="gps" type="button">GPS sample</button></div>
<label>Day <select id="day" aria-label="Day"></select></label><button id="fit" type="button">Fit places</button></div>
<p id="overview" aria-live="polite"></p></header>
<main><section class="map-panel" aria-label="Interactive map"><div id="map"></div>
<p class="map-note">Map features are background context—not your recorded route. Markers represent groups; gaps are never drawn as journeys.</p></section>
<aside><div class="list-heading"><h2 id="list-title">Places</h2><span id="count"></span></div>
<p class="hint">Click a marker or card. Nearby names are candidates; expand a card for separate recorded times.</p><div id="places"></div></aside></main>
<footer><details><summary>Method, source evidence and map credits</summary><ul>'''+''.join('<li>'+e(w)+'</li>' for w in result['warnings'])+'''</ul>
<p>© OpenStreetMap contributors · ODbL 1.0. GeoNames · CC BY 4.0. Natural Earth · public domain. Leaflet · BSD-2-Clause. Leaflet.markercluster · MIT.</p>
<details><summary>Map library licences</summary><pre>'''+e(assets['leaflet_license']+'\n'+assets['cluster_license'])+'''</pre></details></details></footer>
<noscript>This local interactive map needs JavaScript. No scripts or tiles are fetched from the internet.</noscript>
'''+''.join('<script>'+s+'</script>' for s in scripts)+'''</body></html>'''
