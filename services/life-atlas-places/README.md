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

Both sources use one matcher and one report schema. Repeat `--catalogue` for
additional explicitly approved local regions; the **first** catalogue selects
the historical trip rectangle, while each observation uses its covering region.
The map mode below presents grouped places instead of individual source rows.


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
- Venue reference coverage is the selected regions only; GPS stops outside them still get
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
contains sensitive locations and source references. The legacy list uses no
JavaScript. The map uses hash-pinned **embedded** Leaflet/markercluster and an
exact-script-hash CSP, with no remote tiles/fonts/assets or runtime lookups. Opening it
does not call external services. Nothing copies real tracks or archives onto
the laptop or creates a public/shareable URL. Reading the report through a
local browser/tunnel or exporting it is a separate explicit handoff decision.

`setup_reference.py` and `setup_map.py` are the **only networked setup commands**.
They read public package/geographic URLs, never personal locations, the history
index or uploads:

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
on Aether using the isolated venv. Python unit tests use synthetic coordinates
and mocked downloads, not real locations or external requests. Tests cover
shared inputs, source read-only access, gaps/accuracy,
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

## Map-first mode (explicit, still no permanent app)

`setup_map.py` downloads one approved public Geofabrik reference, pinned
[Leaflet 1.9.4](https://leafletjs.com/) (BSD-2-Clause),
[Leaflet.markercluster 1.5.3](https://github.com/Leaflet/Leaflet.markercluster)
(MIT) and [Natural Earth 5.1.2 land](https://www.naturalearthdata.com/about/terms-of-use/)
(public domain). The npm archives are SHA-512 pinned; extracted library files
and the land data are SHA-256 checked by rendering. Only explicit regular tar
members are extracted. No CDN is contacted when opening the report. Existing
receipted public references are verified, not overwritten.

Install `requirements-map-arm64-py313.txt` **only in the existing isolated
build venv**, using `--only-binary=:all: --require-hashes --no-cache-dir`.
Shapely/GEOS and NumPy are build-time geometry tools, not new live services or
report-generation dependencies. This avoids implementing a custom simplifier.

Build a second catalogue with `catalogue.py` for the approved additional region,
retaining the first catalogue. Run `map_context.py` offline against a previous
bounded report and those two public PBFs. It derives the private selection
bounds locally, never sends them to a provider, and retains public reference
hashes. Named towns, road lines, coasts and rivers provide map context. Line
simplification is 35 m for regional context and 3 m for neighbourhood context,
using a local metric approximation; **personal coordinates and venue geometry
are not simplified**. It is not a navigational basemap. Each context is capped
at 300,000 coordinates; an over-budget job fails rather than silently clipping
records or raising memory limits.

Run setup under the same public-only, private-input-inaccessible systemd sandbox
shown above; the following shows its command arguments:

```sh
# Public-only download setup, isolated from private inputs:
python3 /opt/life-atlas-places/setup_map.py \
  --root /srv/life-atlas/places/reference \
  --osm-url 'https://download.geofabrik.de/REGION-latest.osm.pbf' \
  --osm-filename region.osm.pbf

# Offline context (use a new output, with the same resource sandbox as catalogue):
/opt/life-atlas-places/venv/bin/python -B /opt/life-atlas-places/map_context.py \
  --report /srv/life-atlas/places/EXISTING-RUN/report.json \
  --history-map /srv/life-atlas/places/reference/HISTORY.osm.pbf \
  --gps-map /srv/life-atlas/places/reference/GPS.osm.pbf \
  --output /srv/life-atlas/places/NEW-CONTEXT.json
```

For report generation, repeat `--catalogue SECOND-CATALOGUE.sqlite` and add
`--map-reference-dir /srv/life-atlas/places/reference --map-context NEW-CONTEXT.json`
to the offline report command above. Use the system Python, not a live GIS
server. The generated HTML is self-contained; no directory/file server is
required on Aether. Map licences, source records and separate time intervals
are retained in the private output.

Historical records group by Google place ID; differing IDs are not silently
collapsed. GPS records use complete-link grouping: every centre in a group
must be within 100 m of every other centre, avoiding proximity chains. Spatial
groups are **not** counts of verified venues or uninterrupted stays. Unknown
accommodation is not assigned to a nearby school. GPS headings prefer nearby
named roads; other POIs remain clearly qualified candidates. Road hints retain
the immutable context feature index/reference hash. Named landmarks/venues are
preferred to broad land-cover parents for headings as a display preference,
not an invented confidence score; all original candidates remain in details.

The trip appears first, with a map and named cards, not an uncovered GPS dump.
Day filtering applies to both markers **and source segments**. Dense markers
cluster on screen; even at maximum zoom, coincident markers can spiderfy and
remain individually selectable. Source details begin collapsed. Background
roads are not observed routes; no paths are drawn across recording gaps.
Names enter the DOM with `textContent`; embedded JSON escapes script-ending
characters, and CSP hashes cover the exact local scripts. No external API,
weather, telemetry, map tiles or network fallback is configured.

## Browser acceptance

`test_map_browser.mjs` uses an existing Playwright/Chromium, always headless with
an isolated temporary profile. It tests actual painted markers in the first
viewport, context, day/source filters, per-day segments, zoom, dense clusters,
coincident markers at zoom 19, card/raw-detail access and mobile layout. Any
attempted external page request or page error fails the test. Set
`PLAYWRIGHT_PATH` and `MAP_TEST_BROWSER` for the local test environment.

For synthetic fixtures, use `--fixture PATH --screenshot PATH` (the fixture
must explicitly contain `Synthetic data only`). **Screenshots are forbidden
for private report URLs**. A real-data acceptance run uses only the approved
in-memory localhost viewer via `TRAVEL_MAP_TEST_URL`, optionally with
`--expected-gps-groups COUNT`; it does not save a report file or private image.
Temporary browser profiles use the runtime directory and are removed after
headless tests. No user's live browser profile is reused.

## Resource notes

A regional reference build can take tens of minutes at the half-core cap;
this is a one-off batch, not a continuously running geocoder. The tested bounded
legacy list report used about 13 MiB and under one second after spatial-query
optimization. The tested map report used about 227 MiB and seven seconds as a
one-shot batch under the 256 MiB cap; nothing remains running on the VPS.
Those figures are not promises for arbitrary reference regions or samples.

Inspect `systemctl show UNIT -p Result -p MemoryPeak -p CPUUsageNSec` while the
transient unit is loaded, and retain the `systemd-run --wait` result or journal
before successful units unload. Resource limits are safety ceilings, not promised working sets. If a
run exceeds them, investigate rather than silently raising limits. Confirm
source hashes, private permissions, catalogue/report integrity, code hashes
and unchanged importer unit/timer before handoff. No global map server,
whole-history run, automatic analysis or persistent viewer is installed.
