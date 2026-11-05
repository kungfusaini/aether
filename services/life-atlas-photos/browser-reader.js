// Protocol references: MIT Google-Photos-Toolkit api.ts/parser.ts; native
// PhotosSearchItems (EzkLib). Only date-bounded search is implemented here.
async function collectPhotoMetadata(config) {
  const g = window.WIZ_global_data;
  if (location.hostname !== 'photos.google.com' || typeof g?.SNlM0e !== 'string') {
    throw Error('Local Google Photos sign-in is required');
  }
  const inside = day => day != null && day >= config.start && day <= config.end;
  const optionalNumber = (value, max) => {
    if (value == null || value === '') return null;
    if (!(typeof value === 'number' || typeof value === 'string' && /^-?\d+$/.test(value))) throw Error('Unexpected timing field type');
    const n = Number(value);
    if (!Number.isSafeInteger(n) || Math.abs(n) > max) throw Error('Invalid timing field value');
    return n;
  };
  const timestamp = value => {
    const n = optionalNumber(value, 8640000000000000);
    if (n != null && (n < -62135596800000 || n > 253402300799999)) throw Error('Timestamp outside supported calendar range');
    return n;
  };
  const identifier = value => typeof value === 'string' && /^[A-Za-z0-9_+=/\-]{1,1024}$/.test(value);
  const records = new Map(), unresolved = new Map(), queries = [];
  for (const day of config.queryDays) {
    let cursor = null, pages = 0, outside = 0;
    const seen = new Set();
    do {
      const url = new URL(g.eptZe + 'data/batchexecute', location.origin);
      if (url.origin !== location.origin || !url.pathname.includes('/_/PhotosUi/data/batchexecute')) throw Error('Unexpected Photos endpoint');
      for (const [k, v] of Object.entries({rpcids: 'EzkLib', 'source-path': location.pathname, 'f.sid': g.FdrFJe, bl: g.cfb2h, pageId: 'none', rt: 'c'})) if (v != null) url.searchParams.set(k, String(v));
      // ISO calendar date is parsed by Photos; no whole-library endpoint or cursor.
      const query = new Date(day + 'T00:00:00Z').toLocaleDateString('en-US', {timeZone: 'UTC', year: 'numeric', month: 'long', day: 'numeric'});
      const response = await fetch(url, {
        method: 'POST', credentials: 'same-origin', redirect: 'error',
        headers: {'content-type': 'application/x-www-form-urlencoded;charset=UTF-8'},
        body: new URLSearchParams({'f.req': JSON.stringify([[['EzkLib', JSON.stringify([query, null, cursor]), null, 'generic']]]), at: g.SNlM0e}),
        signal: AbortSignal.timeout(15000),
      });
      if (!response.ok) throw Error('Photos search refused: HTTP ' + response.status);
      const text = await response.text();
      if (text.length > 4 * 1024 * 1024) throw Error('Metadata page exceeds safety bound');
      let envelope;
      for (const line of text.split('\n')) {
        if (!line.startsWith('[[')) continue;
        let rows; try { rows = JSON.parse(line); } catch { continue; }
        envelope = rows.find(r => Array.isArray(r) && r[0] === 'wrb.fr' && r[1] === 'EzkLib');
        if (envelope) break;
      }
      if (typeof envelope?.[2] !== 'string') throw Error('Unrecognized Photos response');
      const data = JSON.parse(envelope[2]);
      // Fail closed on schema changes. Only explicit array results with a
      // null/string continuation slot are supported, including [[], null].
      if (!Array.isArray(data) || data.length < 2 || !Array.isArray(data[0]) || (data[1] !== null && typeof data[1] !== 'string')) throw Error('Unrecognized metadata records');
      for (const row of data?.[0] || []) {
        if (!Array.isArray(row) || row.length < 6 || ![0, 1, 2, 3, 4, 5].every(i => Object.hasOwn(row, i)) || !(row[1] === null || Array.isArray(row[1]))) throw Error('Unrecognized provider record structure');
        if (!identifier(row?.[0]) || row[3] != null && !identifier(row[3])) throw Error('Unexpected provider identifier type');
        const t = timestamp(row[2]);
        const offset = optionalNumber(row[4], 14 * 3600000);
        const utcDay = t != null ? new Date(t).toISOString().slice(0, 10) : null;
        const offsetDay = t != null && offset != null ? new Date(t + offset).toISOString().slice(0, 10) : null;
        const matchingDays = [...new Set([utcDay, offsetDay].filter(inside))].sort();
        const tags = Object.assign({}, ...row.filter(v => v && typeof v === 'object' && !Array.isArray(v)));
        const observation = {
          source_id: row[0], provider_dedup_key: row[3] ?? null,
          photo_timestamp_ms: t, recorded_offset_ms: offset,
          item_creation_timestamp_ms: timestamp(row[5]), utc_date: utcDay,
          recorded_offset_date: offsetDay, matching_days: matchingDays,
          query_days: [day], media_type: Number(tags[76647426]?.[0]) > 0 ? 'video' : 'photo',
          timing_unresolved: !matchingDays.length,
        };
        if (!matchingDays.length && t != null && offset != null) { outside++; continue; }
        const store = matchingDays.length ? records : unresolved;
        const prior = store.get(observation.source_id);
        if (prior) {
          if (prior.photo_timestamp_ms !== t || prior.recorded_offset_ms !== offset || prior.item_creation_timestamp_ms !== observation.item_creation_timestamp_ms) throw Error('Metadata changed during collection; no snapshot published');
          if (!prior.query_days.includes(day)) prior.query_days.push(day);
        } else store.set(observation.source_id, observation);
      }
      pages++; cursor = data?.[1] || null;
      if (cursor) {
        if (seen.has(cursor) || pages >= 20) throw Error('Date pagination safety bound reached');
        seen.add(cursor);
      }
      if (records.size + unresolved.size > 20000) throw Error('Metadata record safety bound reached');
    } while (cursor);
    queries.push({day, pages, excluded_known_outside: outside, complete: true});
  }
  // Explicit projection: no thumbnail/download URLs, cookies, anti-CSRF tokens,
  // session IDs, private account profile or raw RPC payload leaves the browser.
  return {records: [...records.values()], unresolved: [...unresolved.values()], queries};
}
