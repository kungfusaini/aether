#!/usr/bin/env python3
"""Explicit setup only: fetch PUBLIC references and an isolated, pinned parser.

This command does not read coordinates, the history index, uploads or credentials.
Never call it from the offline analysis or the existing hourly importer.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlparse
import zipfile

ROOT = Path('/srv/life-atlas/places/reference')
VENV = Path('/opt/life-atlas-places/venv')
PIP_VERSION = '26.3'
PIP_SHA256 = 'f3b60b20ffaadd4415ed7957212a38ac63fa4c5fa375bd0f4a797777940fad13'


def download(url, target, limit, expected_sha256=None):
    target = Path(target)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.download-', delete=False) as f:
        tmp = Path(f.name)
        h = hashlib.sha256()
        size = 0
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                if not response.url.startswith('https://'):
                    raise ValueError('non-HTTPS redirect')
                for block in iter(lambda: response.read(1024*1024), b''):
                    size += len(block)
                    if size > limit:
                        raise ValueError('public download exceeds size cap')
                    h.update(block)
                    f.write(block)
            f.flush()
            os.fsync(f.fileno())
            if expected_sha256 and h.hexdigest() != expected_sha256:
                raise ValueError('wheel hash mismatch')
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, target)
    return dict(url=url, sha256=h.hexdigest(), bytes=size)


def main(osm_url, osm_filename):
    parsed = urlparse(osm_url)
    if parsed.scheme != 'https' or parsed.netloc != 'download.geofabrik.de' or not parsed.path.endswith('.osm.pbf'):
        raise ValueError('choose a public Geofabrik PBF reference URL, not a personal-location query')
    if Path(osm_filename).name != osm_filename or not osm_filename.endswith('.osm.pbf'):
        raise ValueError('OSM filename must be a plain PBF basename')
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(VENV)], check=True)
    python = str(VENV/'bin/python')
    # Debian may lack ensurepip. Bootstrap only inside this venv using a pinned,
    # hash-verified pure-Python pip wheel, never apt or global site-packages.
    with urllib.request.urlopen(f'https://pypi.org/pypi/pip/{PIP_VERSION}/json', timeout=30) as response:
        info = json.load(response)
    wheel = next(f for f in info['urls'] if f['filename'].endswith('py3-none-any.whl'))
    pip_path = ROOT/'pip-bootstrap.whl'
    download(wheel['url'], pip_path, 5*1024*1024, PIP_SHA256)
    site = subprocess.check_output([python, '-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])'], text=True).strip()
    with zipfile.ZipFile(pip_path) as archive:
        if any(Path(n).is_absolute() or '..' in Path(n).parts for n in archive.namelist()):
            raise ValueError('unsafe wheel path')
        archive.extractall(site)
    subprocess.run([python, '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
        '--no-input', '--no-cache-dir', '--only-binary=:all:', '--require-hashes',
        '-r', str(Path(__file__).with_name('requirements-arm64-py313.txt'))], check=True)
    receipt = ROOT/'downloads.json'
    references = json.loads(receipt.read_text()) if receipt.exists() else {}
    for filename, url, cap in [
        (osm_filename, osm_url, 400*1024*1024),
        ('cities1000.zip', 'https://download.geonames.org/export/dump/cities1000.zip', 30*1024*1024),
    ]:
        path = ROOT/filename
        if path.exists():
            previous = references.get(filename)
            h = hashlib.sha256()
            with path.open('rb') as f:
                for chunk in iter(lambda: f.read(1024*1024), b''):
                    h.update(chunk)
            if (not previous or previous.get('url') != url or h.hexdigest() != previous['sha256']
                    or path.stat().st_size != previous['bytes']):
                raise FileExistsError('unreceipted/changed reference; preserve it and investigate before retrying')
            continue
        references[filename] = download(url, path, cap)
        references[filename]['downloaded_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
        pending = receipt.with_suffix('.pending')
        pending.write_text(json.dumps(references, indent=2))
        os.replace(pending, receipt)
    print(json.dumps(references))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--osm-url', required=True)
    p.add_argument('--osm-filename', default='region.osm.pbf')
    args = p.parse_args()
    main(args.osm_url, args.osm_filename)
