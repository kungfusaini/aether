# Offline places pilot

A one-shot analysis/report layer above the existing private travel index. It
has **no web server, background daemon, new timer, database server or phone
integration**. It never changes `life-atlas-history` or its inputs. Code and
synthetic fixtures belong in Git; reference data, catalogues and reports do not.

## Scope and interpretation

This pilot reads Google-inferred visits overlapping an explicitly selected
window of at most fourteen days, restricted to the reference rectangle, plus
an explicitly selected GPSLogger window of at most three days / 50,000 points.
Dates, reference region and display timezone are operator inputs, never personal
itinerary constants committed in source. It does not inventory all historical
points or rebuild the whole chronology.

Both sources use one matcher and one report schema:

- Google's visits retain asset ID, original locator, opaque Google place ID,
  hierarchy and source probabilities. They remain Google's inferences; nested
  visits overlap and must not be summed as independent stays.
- GPS candidate stops use chronological points, a fixed anchor, a 75 m radius,
  at least three points spanning ten minutes, and no consecutive gap over ten
  minutes. Anchor displacement plus both reported accuracy radii must fit the
  radius. Accuracy above 100 m or unusable coordinates split the sequence.
  Missing accuracy remains explicitly unknown. This conservative rule can miss
  genuine stops; it does not establish uninterrupted attendance between fixes.
  Polar/antimeridian GPS stop clusters are deliberately unsupported in this pilot.
- Current named OSM nodes and areas provide **candidate** venues, based on area
  containment or distance within 100 m of a mapped point/boundary. Multipolygon
  holes are respected. Areas intersecting the selected rectangle are retained
  even when their representative centre is outside it. An area bounding-box centre is only a representative
  search point, never claimed to be an entrance. Non-area ways and unnamed
  objects are omitted. Nearby candidates can be completely wrong.
- GeoNames provides the **nearest listed locality**, including its country code,
  not municipality or country-boundary containment. `cities1000` omits many
  small settlements. Report grouping is explicitly “near town”, not “visited
  this town/country”. The reference does not establish verified country counts.
- Venue reference coverage is the selected region only; GPS stops outside it still get
  worldwide nearby-locality labels, with **venues not searched**. Broader venue
  coverage requires a separately chosen regional extract, not online fallbacks.
- Current map data cannot prove a venue's historical name, existence or attendance.
  No Google API/name resolution is performed. Unknowns stay unknown; there are
  no synthetic confidence percentages, transport claims or confirmed overrides.

## Build versus adopt

[GeoPulse](https://geopulse.cc/) and [Dawarich](https://dawarich.app/) are full
application stacks; this pilot deliberately does not install either.
[MovingPandas](https://movingpandas.readthedocs.io/en/main/api/trajectorystopdetector.html)
has maintained stop detection but brings a broader GeoPandas/Shapely/matplotlib
stack. A bounded standard-library stop detector with synthetic regressions is
sufficient for this pilot, not a claim to outperform those tools.

Use maintained [pyosmium](https://docs.osmcode.org/pyosmium/latest/) (BSD-2-Clause)
for OSM parsing rather than implementing PBF or multipolygon assembly. A
file-backed node index constrains parser cache RAM; SQLite RTree handles local
candidate lookup. Only catalogue building requires pyosmium; report generation
uses the system Python standard library.

## Storage and privacy

All real processing takes place on Aether:

- Code: `/opt/life-atlas-places/` (root-owned).
- Isolated pinned parser environment: `/opt/life-atlas-places/venv/`.
- Public downloaded reference data and receipts:
  `/srv/life-atlas/places/reference/`.
- Derived catalogue: `/srv/life-atlas/places/catalogue.sqlite`.
- Private run directory: `/srv/life-atlas/places/pilot-<run>/`, containing
  `report.html`, `report.json` and SHA-256 `manifest.json`.
- Authoritative **read-only** input:
  `/srv/life-atlas/history/history.sqlite`.

Directory mode 0700, files 0600; all under the existing root-private tree and
outside web roots. The upload account must not read these outputs. The report
contains sensitive locations and source references. It has escaped HTML, no
JavaScript, no map tiles/fonts/remote assets, and a restrictive CSP. Opening it
does not call external services. Nothing copies real tracks or archives onto
the laptop or creates a public/shareable URL. Reading the report through a
local browser/tunnel or exporting it is a separate explicit handoff decision.

`setup_reference.py` is the **only networked command**. It reads public package
and geographic URLs, never personal locations, the history index or uploads:

- An explicitly selected [Geofabrik regional PBF](https://download.geofabrik.de/),
  streamed locally with a chosen selection rectangle; maximum download 400 MiB.
- [GeoNames cities1000](https://download.geonames.org/export/dump/readme.txt),
  approximately 11 MB compressed, worldwide nearby-locality coverage.

Downloads have byte caps, SHA-256 receipts and atomic publication. Parser
wheels are exact-version/hash pinned for Aether's ARM64 Python 3.13. Debian's
missing ensurepip is handled by a hash-pinned pip wheel **inside the venv**, not
system package installation. Existing receipted references are verified/reused;
unreceipted or changed files require investigation instead of overwriting.
Catalogue schema version 2 records the area-intersection selection semantics.
An existing catalogue is reused only after checking reference hashes, selection
settings, SQLite integrity and bounds coverage. A changed reference requires a
new output path, not overwriting a previous catalogue. Summary output tolerates
a detached pipe after successful publication. Reference refresh, pruning and
scheduling are deliberately absent.

OSM attribution: **© OpenStreetMap contributors, ODbL 1.0**
([terms](https://www.openstreetmap.org/copyright)). GeoNames: **CC BY 4.0**
([terms](https://www.geonames.org/about.html)). The report retains attribution,
input reference hashes and OSM object/GeoNames IDs. Any later distribution of
derived geographic databases must respect their licences; no publication is
part of this pilot.

## Tests

```sh
python3 -B -m unittest discover -s services/life-atlas-places -v
```

The real-parser synthetic node/area test skips without pyosmium; run all tests
on Aether using the isolated venv. No tests use real coordinates or network
requests. Tests cover shared inputs, source read-only access, gaps/accuracy,
geometry/holes, date-line locality lookup, timezone normalization, private
output modes, escaping/CSP and download failures.

## Explicit deployment and one-shot execution

Only after approval, copy these source files to the root-owned `/opt` directory.
Do not call `life-atlas-history/deploy.py` or modify its unit/timer. Do not push
private data or commit generated files. Each run is explicit; nothing below
creates an enabled systemd service.

Setup (public-only network access, private inputs inaccessible):

```sh
systemd-run --unit=life-atlas-places-reference --wait --pipe \
  -p MemoryMax=384M -p CPUQuota=50% -p Nice=10 -p UMask=0077 \
  -p 'InaccessiblePaths=/srv/life-atlas/history /srv/life-atlas/google-timeline /srv/gpslogger' \
  /usr/bin/python3 -B /opt/life-atlas-places/setup_reference.py \
  --osm-url 'https://download.geofabrik.de/REGION-latest.osm.pbf'
```

Catalogue build (no network, cap 512 MiB, half one CPU core). Use journal output
rather than `--pipe` for this potentially long-running operation so a detached
SSH client cannot break output publication:

```sh
systemd-run --unit=life-atlas-places-catalogue \
  -p MemoryMax=512M -p MemorySwapMax=0 -p CPUQuota=50% -p Nice=10 \
  -p UMask=0077 -p PrivateNetwork=yes -p PrivateTmp=yes \
  -p ProtectSystem=strict -p ProtectHome=yes \
  -p ReadWritePaths=/srv/life-atlas/places \
  -p 'InaccessiblePaths=/srv/life-atlas/history /srv/life-atlas/google-timeline /srv/gpslogger' \
  /opt/life-atlas-places/venv/bin/python -B /opt/life-atlas-places/catalogue.py \
  --osm /srv/life-atlas/places/reference/region.osm.pbf \
  --towns /srv/life-atlas/places/reference/cities1000.zip \
  --output /srv/life-atlas/places/catalogue.sqlite \
  --bbox MINLON MINLAT MAXLON MAXLAT \
  --osm-url 'https://download.geofabrik.de/REGION-latest.osm.pbf'
```

Offline report (substitute approved windows/zone and use a new run directory):

```sh
systemd-run --unit=life-atlas-places-report --wait --pipe \
  -p MemoryMax=256M -p MemorySwapMax=0 -p CPUQuota=50% -p Nice=10 \
  -p UMask=0077 -p PrivateNetwork=yes -p PrivateTmp=yes \
  -p ProtectSystem=strict -p ProtectHome=yes \
  -p ReadWritePaths=/srv/life-atlas/places \
  /usr/bin/python3 -B /opt/life-atlas-places/places.py \
  --history /srv/life-atlas/history/history.sqlite \
  --catalogue /srv/life-atlas/places/catalogue.sqlite \
  --gps-start GPS_START_UTC --gps-end GPS_END_UTC \
  --google-start GOOGLE_START_UTC --google-end GOOGLE_END_UTC \
  --google-timezone DISPLAY_TIMEZONE \
  --output /srv/life-atlas/places/pilot-RUN
```

A regional reference build can take tens of minutes at the half-core cap;
this is a one-off batch, not a continuously running geocoder. The tested bounded
report used about 13 MiB and under one second after spatial-query optimization;
those figures are not a promise for arbitrary reference regions or samples.

Inspect `systemctl show UNIT -p Result -p MemoryPeak -p CPUUsageNSec` while the
transient unit is loaded, and retain the `systemd-run --wait` result or journal
before successful units unload. Resource limits are safety ceilings, not promised working sets. If a
run exceeds them, investigate rather than silently raising limits. Confirm
source hashes, private permissions, catalogue/report integrity, code hashes
and unchanged importer unit/timer before handoff. No global map server,
whole-history run, automatic analysis or persistent viewer is installed.
