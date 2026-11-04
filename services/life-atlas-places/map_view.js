'use strict';
// Everything below consumes the embedded report. No fetch, tile URL or API key.
const map = L.map('map', {preferCanvas: true, zoomControl: true, minZoom: 3, maxZoom: 19});
map.attributionControl.setPrefix(false);
map.attributionControl.addAttribution('OSM contributors · Natural Earth · Leaflet');
const land = L.geoJSON(REPORT.land, {
  style: {color: '#bbcabd', weight: .5, fillColor: '#f4f2e9', fillOpacity: 1}, interactive: false
}).addTo(map);
const contextLayer = L.layerGroup().addTo(map);
const markers = L.markerClusterGroup({maxClusterRadius: 38, showCoverageOnHover: false,
  spiderfyOnMaxZoom: true, animate: false});
map.addLayer(markers);
const day = document.getElementById('day');
const list = document.getElementById('places');
let source = 'google_timeline';
let selected = null;
let visible = [];
const markerById = new Map();

function text(tag, value, className) {
  const element = document.createElement(tag);
  element.textContent = value;
  if (className) element.className = className;
  return element;
}
function append(parent, ...children) { for (const child of children) parent.appendChild(child); return parent; }
function fmt(value, timezone, full = false) {
  return new Intl.DateTimeFormat('en-GB', {timeZone: timezone, day: 'numeric', month: 'short',
    ...(full ? {year: 'numeric'} : {}), hour: '2-digit', minute: '2-digit', timeZoneName: 'short'}).format(new Date(value));
}
function records(group) {
  const rows = group.record_indices.map(i => REPORT.visits[i]);
  return day.value === 'all' ? rows : rows.filter(v => v.display_days.includes(day.value));
}
function locality(group) {
  const town = group.locality;
  return town ? `Near ${town.name} · ${town.country_code || 'country unresolved'}` : 'Locality unresolved';
}
function popup(group) {
  const node = document.createElement('div');
  append(node, text('strong', group.title), text('p', locality(group)),
    text('p', `${records(group).length} recorded segments in selection; gaps retained.`));
  const button = text('button', 'Show recorded times');
  button.type = 'button'; button.addEventListener('click', () => select(group, true)); node.appendChild(button);
  return node;
}
function select(group, openDetails = false) {
  selected = group.id;
  document.querySelectorAll('.place-card').forEach(card => card.classList.toggle('selected', card.dataset.id === selected));
  const card = document.getElementById(group.id);
  if (openDetails && card) {
    card.querySelector('details').open = true;
    card.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  }
  const marker = markerById.get(group.id);
  if (marker) markers.zoomToShowLayer(marker, () => marker.openPopup());
}
function card(group, index) {
  const article = document.createElement('article'); article.className = 'place-card';
  article.id = group.id; article.dataset.id = group.id;
  const title = text('button', `${index + 1}. ${group.title}`, 'place-title');
  title.type = 'button'; title.addEventListener('click', () => select(group));
  append(article, title, text('p', locality(group), 'locality'), text('p', group.title_basis, 'uncertainty'));
  const items = records(group);
  const shownDays = day.value === 'all' ? group.days : [day.value];
  const daysLabel = shownDays.map(d => new Intl.DateTimeFormat('en-GB', {day: 'numeric', month: 'short',timeZone:'UTC'}).format(new Date(d+'T12:00:00Z'))).join(', ');
  article.appendChild(text('p', `${items.length} source segment${items.length === 1 ? '' : 's'} · ${daysLabel}`, 'segment-summary'));
  const details = document.createElement('details');
  details.appendChild(text('summary', 'Recorded times & nearby names'));
  details.appendChild(text('p', group.grouping_note + '. Separate times below are not one continuous stay.', 'hint'));
  const times = document.createElement('ul'); times.className = 'recorded-times';
  for (const v of items) {
    const li = text('li', `${fmt(v.start_utc, v.display_timezone, true)} → ${fmt(v.end_utc, v.display_timezone, true)}`);
    if (v.hierarchy_level > 0) li.appendChild(text('small', 'Nested Google record; may overlap its parent'));
    if (v.ends_because) li.appendChild(text('small', `Segment ended: ${v.ends_because}`));
    times.appendChild(li);
  }
  details.appendChild(times);
  if (group.candidates.length) {
    details.appendChild(text('h3', 'Nearby map names—not confirmed venues'));
    const names = document.createElement('ul');
    for (const p of group.candidates) names.appendChild(text('li', `${p.name} · ${p.subtype || p.category} · ${p.distance_m} m`));
    details.appendChild(names);
  } else details.appendChild(text('p', 'No named venue match. This can be an unmapped or private location.'));
  const technical = document.createElement('details'); technical.className = 'technical';
  append(technical, text('summary', 'Original source references'), text('pre', JSON.stringify({grouping:group.grouping_note,street_reference:group.street_reference,records:items}, null, 2)));
  details.appendChild(technical); article.appendChild(details);
  return article;
}
function drawContext() {
  contextLayer.clearLayers();
  const context = REPORT.map_context[source];
  if (!context) return;
  const zoom = map.getZoom();
  const viewport = map.getBounds().pad(.15);
  L.geoJSON(context, {
    filter(feature) {
      const p = feature.properties, geometry = feature.geometry;
      const b = p.bounds;
      if (b && (b[2]<viewport.getWest() || b[0]>viewport.getEast() || b[3]<viewport.getSouth() || b[1]>viewport.getNorth())) return false;
      if (geometry.type === 'Point' && !viewport.contains([geometry.coordinates[1],geometry.coordinates[0]])) return false;
      if (p.role === 'town') {
        const minimum = {city:3,town:9,village:11,suburb:13,neighbourhood:15};
        return zoom >= (minimum[p.subtype] || 13);
      }
      if (p.role === 'road') {
        if (p.subtype.startsWith('tertiary')) return zoom >= 12;
        if (p.subtype.startsWith('secondary')) return zoom >= 10;
        if (!['motorway','trunk','primary','motorway_link','trunk_link','primary_link'].includes(p.subtype)) return zoom >= 13;
      }
      return true;
    },
    style(feature) {
      const role = feature.properties.role;
      return {color: role === 'road' ? '#c9b899' : '#80adbe', weight: role === 'road' ? 1.4 : 1, opacity: .8};
    },
    pointToLayer(feature, latlng) {
      return L.circleMarker(latlng, {radius: 1.5, color: '#6c796d', fillOpacity: .7, weight: 1});
    },
    onEachFeature(feature, layer) {
      const p = feature.properties;
      if (!p.name) return;
      layer.bindTooltip(text('span', p.name), {permanent: p.role === 'town', direction: 'top', className: p.role === 'town' ? 'town-label' : 'road-label'});
    }
  }).addTo(contextLayer);
}
function fit() {
  map.stop();
  if (visible.length) map.fitBounds(visible.map(g => [g.latitude,g.longitude]), {padding: [35,35], maxZoom: 16, animate:false});
  else map.setView([20,0],3,{animate:false});
}
function render() {
  markers.clearLayers(); markerById.clear(); selected = null;
  visible = REPORT.view_groups.filter(g => g.source === source && (day.value === 'all' || g.days.includes(day.value)));
  visible.sort((a,b) => records(a)[0].start_utc.localeCompare(records(b)[0].start_utc));
  list.replaceChildren(...visible.map(card));
  if (!visible.length) list.appendChild(text('p', 'No recorded place groups in this selection.'));
  visible.forEach((g,i) => {
    // This HTML is generated from a numeric index only, never place text.
    const icon = L.divIcon({className: 'place-marker', html: `<span>${i+1}</span>`, iconSize: [30,30], iconAnchor: [15,15]});
    const marker = L.marker([g.latitude,g.longitude], {icon, title: g.title, keyboard: true});
    marker.bindPopup(popup(g)); marker.on('click', () => select(g));
    markers.addLayer(marker); markerById.set(g.id, marker);
  });
  const raw = visible.reduce((n,g) => n+records(g).length,0);
  document.getElementById('count').textContent = `${visible.length} groups`;
  document.getElementById('list-title').textContent = source === 'gpslogger' ? 'Recorded areas' : 'Possible places';
  const activeDays = day.value === 'all' ? [...new Set(visible.flatMap(g => g.days))].sort() : [day.value];
  const dateLabel = d => new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',day:'numeric',month:'short',year:'numeric'}).format(new Date(d+'T12:00:00Z'));
  const period = activeDays.length ? dateLabel(activeDays[0]) + (activeDays.length > 1 ? ' – '+dateLabel(activeDays.at(-1)) : '') : '';
  document.getElementById('overview').textContent = `${source === 'gpslogger' ? 'GPS sample' : 'Historical trip'} · ${period} · ${visible.length} place groups from ${raw} source segments. Not confirmed destinations.`;
  document.getElementById('history').setAttribute('aria-pressed', String(source === 'google_timeline'));
  document.getElementById('gps').setAttribute('aria-pressed', String(source === 'gpslogger'));
  fit(); drawContext();
}
function setSource(value) {
  source = value;
  const dates = [...new Set(REPORT.view_groups.filter(g => g.source === source).flatMap(g => g.days))].sort();
  const all = text('option', 'All days'); all.value = 'all';
  day.replaceChildren(all, ...dates.map(d => {const label = new Intl.DateTimeFormat('en-GB', {timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'}).format(new Date(d+'T12:00:00Z')); const o = text('option', label); o.value = d; return o;}));
  // Begin with the trip, not an uncovered current-location dump. All days
  // gives a regional overview; marker clustering handles dense city points.
  day.value = 'all'; render();
}
document.getElementById('history').addEventListener('click', () => setSource('google_timeline'));
document.getElementById('gps').addEventListener('click', () => setSource('gpslogger'));
document.getElementById('fit').addEventListener('click', fit);
day.addEventListener('change', render);
map.on('moveend', drawContext);
new ResizeObserver(() => {map.invalidateSize({pan:false}); fit();}).observe(document.getElementById('map'));
setSource(REPORT.view_groups.some(g => g.source === 'google_timeline') ? 'google_timeline' : 'gpslogger');
// Local introspection for headless acceptance tests, never a remote API.
window.travelMap = {map, setSource, get visibleGroups() {return visible;}, markers};
