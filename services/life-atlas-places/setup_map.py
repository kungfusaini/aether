#!/usr/bin/env python3
"""Explicit public-only map assets setup; never reads personal locations."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
from urllib.parse import urlparse

from setup_reference import download

LEAFLET_URL = 'https://registry.npmjs.org/leaflet/-/leaflet-1.9.4.tgz'
LEAFLET_SHA512 = 'nxS1ynzJOmOlHp+iL3FyWqK89GtNL8U8rvlMOsQdTTssxZwCXh8N2NB3GDQOL+YR3XnWyZAxwQixURb+FA74PA=='
CLUSTER_URL = 'https://registry.npmjs.org/leaflet.markercluster/-/leaflet.markercluster-1.5.3.tgz'
CLUSTER_SHA512 = 'vPTw/Bndq7eQHjLBVlWpnGeLa3t+3zGiuM7fJwCkiMFq+nmRuG3RI3f7f4N4TDX7T4NpbAXpR2+NTRSEGfCSeA=='
LAND_URL = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_50m_land.geojson'
LAND_SHA256 = 'e874b27a51d146452be360cafb3cc50c86001074a67d534113e6534682f9826b'


def setup(root, osm_url, filename):
    os.umask(0o077)
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    parsed = urlparse(osm_url)
    if parsed.scheme != 'https' or parsed.netloc != 'download.geofabrik.de' or not parsed.path.endswith('.osm.pbf'):
        raise ValueError('public Geofabrik reference required, never a coordinate lookup')
    if Path(filename).name != filename or not filename.endswith('.osm.pbf'):
        raise ValueError('plain reference filename required')
    receipt_path = root/'map-downloads.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}

    def fetch(name, url, cap, expected=None):
        path = root/name
        if path.exists():
            prior = receipt.get(name)
            h = hashlib.sha256()
            with path.open('rb') as f:
                for block in iter(lambda: f.read(1024*1024), b''):
                    h.update(block)
            if (not prior or prior['url'] != url or h.hexdigest() != prior['sha256']
                    or path.stat().st_size != prior['bytes'] or expected and h.hexdigest() != expected):
                raise ValueError('changed/unreceipted map asset; preserve and investigate')
            return path
        receipt[name] = download(url, path, cap, expected)
        pending = receipt_path.with_suffix('.pending')
        pending.write_text(json.dumps(receipt, indent=2))
        os.replace(pending, receipt_path)
        return path

    fetch(filename, osm_url, 400*1024*1024)
    archive_path = fetch('leaflet-1.9.4.tgz', LEAFLET_URL, 2*1024*1024)
    payload = archive_path.read_bytes()
    if base64.b64encode(hashlib.sha512(payload).digest()).decode() != LEAFLET_SHA512:
        raise ValueError('Leaflet archive integrity mismatch')
    assets = root/'ui'
    assets.mkdir(mode=0o700, exist_ok=True)
    # Extract only explicit regular files, not arbitrary tar paths or links.
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
        for src, dst in [('package/dist/leaflet.js','leaflet.js'),
                         ('package/dist/leaflet.css','leaflet.css'),('package/LICENSE','LEAFLET-LICENSE')]:
            member = archive.getmember(src)
            if not member.isfile() or member.size > 1024*1024:
                raise ValueError('unexpected Leaflet asset')
            (assets/dst).write_bytes(archive.extractfile(member).read())
    cluster_path=fetch('leaflet.markercluster-1.5.3.tgz',CLUSTER_URL,2*1024*1024)
    cluster=cluster_path.read_bytes()
    if base64.b64encode(hashlib.sha512(cluster).digest()).decode()!=CLUSTER_SHA512:
        raise ValueError('marker-cluster archive integrity mismatch')
    with tarfile.open(fileobj=io.BytesIO(cluster),mode='r:gz') as archive:
        for src,dst in [('package/dist/leaflet.markercluster.js','markercluster.js'),
                        ('package/dist/MarkerCluster.css','markercluster.css'),
                        ('package/dist/MarkerCluster.Default.css','markercluster-default.css'),
                        ('package/MIT-LICENCE.txt','MARKERCLUSTER-LICENSE')]:
            member=archive.getmember(src)
            if not member.isfile() or member.size>1024*1024:raise ValueError('unexpected cluster asset')
            (assets/dst).write_bytes(archive.extractfile(member).read())
    fetch('land-50m.geojson', LAND_URL, 3*1024*1024, LAND_SHA256)
    print(json.dumps({'public_reference_files': len(receipt), 'local_leaflet_version': '1.9.4'}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True)
    p.add_argument('--osm-url', required=True)
    p.add_argument('--osm-filename', required=True)
    args = p.parse_args()
    setup(args.root, args.osm_url, args.osm_filename)
