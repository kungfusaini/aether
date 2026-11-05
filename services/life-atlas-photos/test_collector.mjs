import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {dateWindow} from './collect.mjs';
const code = await fs.readFile(new URL('./browser-reader.js', import.meta.url), 'utf8');
const config = dateWindow('2022-06-08', '2022-06-15');
assert.equal(config.queryDays[0], '2022-06-07');assert.equal(config.queryDays.at(-1), '2022-06-16');
assert.throws(() => dateWindow('2022-02-30', '2022-03-01'));
assert.throws(() => dateWindow('2022-06-15', '2022-06-08'));
assert.throws(() => dateWindow('2022-01-01', '2022-12-31'));
assert.equal(dateWindow('2024-02-29', '2024-02-29').queryDays.length, 3);
const row = (id, stamp, offset) => [id, ['https://lh3.googleusercontent.com/private-photo-canary'], stamp == null ? null : Date.parse(stamp), 'dedup-' + id, offset, Date.parse('2026-01-01T10:00:00Z'), null, {}];
const duplicate = row('both-days', '2022-06-09T23:30:00Z', 7200000);
const fixtures = new Map([
 [7, [row('lower-edge', '2022-06-07T23:30:00Z', 7200000), row('outside-low', '2022-06-07T12:00:00Z', 7200000)]],
 [8, [row('missing-time', null, null), row('zero-offset', '2022-06-08T12:00:00Z', 0)]],
 [9, [duplicate]], [10, [duplicate]],
 [15, [row('missing-offset', '2022-06-15T10:00:00Z', null)]],
 [16, [row('regression', '2022-06-15T23:40:00Z', 3600000), row('negative-edge', '2022-06-16T00:30:00Z', -7200000), row('half-hour', '2022-06-15T23:30:00Z', 19800000)]],
]);
let requests = 0;
const fetch = async (url, options) => {
 requests++;assert.equal(new URL(url).hostname, 'photos.google.com');assert.equal(new URL(url).searchParams.get('rpcids'), 'EzkLib');assert.equal(options.credentials, 'same-origin');assert.equal(options.redirect, 'error');
 const args = JSON.parse(JSON.parse(options.body.get('f.req'))[0][0][1]);const day = Number(args[0].match(/June (\d+)/)[1]);assert.ok(day >= 7 && day <= 16);
 const rows = day === 16 && args[2] ? [row('outside-high', '2022-06-16T10:00:00Z', 3600000)] : fixtures.get(day) || [];
 return {ok: true, text: async () => JSON.stringify([['wrb.fr', 'EzkLib', JSON.stringify([rows, day === 16 && !args[2] ? 'next' : null])]])};
};
async function run(transport = fetch) {
 return await vm.runInNewContext('(' + code + ')(' + JSON.stringify(config) + ')', {window: {WIZ_global_data: {SNlM0e: 'never-export-token-canary', eptZe: '/_/PhotosUi/'}}, location: {hostname: 'photos.google.com', origin: 'https://photos.google.com', pathname: '/search/test'}, URL, URLSearchParams, AbortSignal, fetch: transport}, {timeout: 1000});
}
const data = await run();assert.equal(requests, 11);assert.equal(data.records.length, 7);assert.equal(data.unresolved.length, 1);
const find = id => data.records.find(r => r.source_id === id);
assert.deepEqual([...find('regression').matching_days], ['2022-06-15']);assert.equal(find('regression').recorded_offset_date, '2022-06-16');assert.equal(find('regression').photo_timestamp_ms, 1655336400000);
assert.deepEqual([...find('both-days').matching_days], ['2022-06-09', '2022-06-10']);assert.deepEqual([...find('both-days').query_days], ['2022-06-09', '2022-06-10']);
assert.equal(find('outside-low'), undefined);assert.equal(find('outside-high'), undefined);assert.equal(find('zero-offset').recorded_offset_ms, 0);assert.equal(find('missing-offset').recorded_offset_date, null);assert.equal(data.unresolved[0].photo_timestamp_ms, null);assert.equal(data.unresolved[0].item_creation_timestamp_ms, Date.parse('2026-01-01T10:00:00Z'));
assert.ok(!JSON.stringify(data).includes('canary'));assert.ok(!JSON.stringify(data).includes('googleusercontent'));
for (const malformed of [{}, null, [], [null, null], [[], {}], [[], 123]]) await assert.rejects(run(async () => ({ok: true, text: async () => JSON.stringify([['wrb.fr', 'EzkLib', JSON.stringify(malformed)]])})), /Unrecognized/);
for (const [field, value] of [[2, []], [2, true], [4, false], [4, {}], [3, {thumb:'https://example.test/url-canary'}], [3, 'https://example.test/url-canary'], [0, 'https://example.test/url-canary']]) {
 const malformed = row('bad', '2022-06-15T12:00:00Z', 3600000);malformed[field] = value;
 await assert.rejects(run(async () => ({ok:true,text:async()=>JSON.stringify([['wrb.fr','EzkLib',JSON.stringify([[malformed],null])]])})), /timing field|identifier type/);
}
for (const malformed of [['synthetic-id'], ['synthetic-id', null, null, null, null], ['synthetic-id', {}, null, null, null, null]]) await assert.rejects(run(async () => ({ok:true,text:async()=>JSON.stringify([['wrb.fr','EzkLib',JSON.stringify([[malformed],null])]])})), /record structure/);
assert.throws(() => dateWindow('0000-01-01', '0000-01-02'));
let deniedCalls = 0;await assert.rejects(run(async () => {deniedCalls++;return {ok: false, status: 403};}), /refused/);assert.equal(deniedCalls, 1);
await assert.rejects(run(async () => ({ok: true, text: async () => JSON.stringify([['wrb.fr', 'EzkLib', JSON.stringify([[], 'repeat'])]])})), /pagination/);
console.log('Collector: bounded dates, both edges, offsets, unknowns, pagination, deduplication, denial/no retry and no credential/image-URL export passed (synthetic only).');
