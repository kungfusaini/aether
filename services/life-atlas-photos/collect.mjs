#!/usr/bin/env node
// Metadata only: never returns private image bytes, URLs or Google session data.
import fs from 'node:fs/promises';
import crypto from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {inspectOriginalBytes} from './audit.mjs';

export function dateWindow(start, end) {
  const parse = value => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) throw Error('Use explicit YYYY-MM-DD dates');
    const n = Date.parse(value + 'T00:00:00Z');
    if (value.startsWith('0000-') || !Number.isFinite(n) || new Date(n).toISOString().slice(0, 10) !== value) throw Error('Invalid calendar date');
    return n;
  };
  const a = parse(start), b = parse(end);
  if (b < a || b - a > 30 * 86400000) throw Error('Requested window must be between 1 and 31 days');
  const queryDays = [];
  for (let d = a - 86400000; d <= b + 86400000; d += 86400000) queryDays.push(new Date(d).toISOString().slice(0, 10));
  return {start, end, queryDays};
}

async function connect(sessionPath) {
  const stat = await fs.stat(sessionPath);
  if ((stat.mode & 0o077) || (process.getuid && stat.uid !== process.getuid())) throw Error('Session file must be owner-private');
  const session = JSON.parse(await fs.readFile(sessionPath, 'utf8'));
  if (!Number.isInteger(session.port) || session.port < 1 || session.port > 65535) throw Error('Invalid local browser port');
  const targets = await (await fetch('http://127.0.0.1:' + session.port + '/json/list', {signal: AbortSignal.timeout(5000)})).json();
  const target = targets.find(t => t.type === 'page' && new URL(t.url).hostname === 'photos.google.com' && (!session.pilotTargetId || t.id === session.pilotTargetId));
  if (!target) throw Error('Open Google Photos in the dedicated local browser and complete any human sign-in');
  const endpoint = new URL(target.webSocketDebuggerUrl);
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname)) throw Error('Browser endpoint must be loopback-only');
  const ws = new WebSocket(endpoint);
  await new Promise((resolve, reject) => {ws.addEventListener('open', resolve, {once: true}); ws.addEventListener('error', () => reject(Error('Browser connection failed')), {once: true});});
  let id = 0; const pending = new Map();
  ws.addEventListener('message', e => {const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) {pending.get(m.id)(m); pending.delete(m.id);}});
  const evaluate = expression => new Promise((resolve, reject) => {
    const n = ++id;
    pending.set(n, m => {
      if (m.error || m.result?.exceptionDetails) reject(Error('Browser metadata evaluation failed; nothing published'));
      else resolve(m.result.result.value);
    });
    ws.send(JSON.stringify({id: n, method: 'Runtime.evaluate', params: {expression, returnByValue: true, awaitPromise: true}}));
    setTimeout(() => {if (pending.delete(n)) reject(Error('Browser metadata evaluation timed out; nothing published'));}, 300000).unref();
  });
  return {evaluate, close: () => ws.close()};
}

async function attachAudits(browser, manifestPath, exiftool, records) {
  const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
  if (!Array.isArray(manifest.files) || !manifest.files.length || manifest.files.length > 10) throw Error('Audit must contain one to ten existing original samples');
  const refs = await browser.evaluate(`(window.__lifeAtlasAlbumAudit?.originalSample||[]).map(p=>({source_id:p.key,timestamp:p.timestamp,filename:p.fileName}))`);
  const audits = [], originals = [];
  for (const file of manifest.files) {
    const ref = refs[file.sample - 1];
    if (!ref || ref.filename !== file.providerFilename || Number(ref.timestamp) !== Number(file.photoTimestamp) || !records.some(r => r.source_id === ref.source_id)) throw Error('Original audit identity does not match the active bounded sample');
    if (!/^\d{2}\.(jpg|png|webp|heic|tiff)$/.test(file.file)) throw Error('Invalid audit file path');
    const bytes = await fs.readFile(path.join(path.dirname(manifestPath), file.file));
    if (crypto.createHash('sha256').update(bytes).digest('hex') !== file.sha256) throw Error('Audited original changed');
    originals.push({bytes});
    audits.push({source_id: ref.source_id, filename: file.providerFilename, sha256: file.sha256,
      bytes: bytes.length, provider_size_matches: file.declaredSizeMatches,
      whatsapp_filename_hint: /WA\d+/i.test(file.providerFilename),
      originals_archived: false});
  }
  const inspected = await inspectOriginalBytes(originals, exiftool);
  return audits.map((audit, i) => ({...audit, ...inspected[i]}));
}

async function main() {
  const {values} = parseArgs({options: {session: {type: 'string'}, start: {type: 'string'}, end: {type: 'string'}, 'audit-manifest': {type: 'string'}, exiftool: {type: 'string', default: 'exiftool'}}});
  const config = dateWindow(values.start, values.end);
  if (!values.session) throw Error('An explicit private local --session file is required');
  const browser = await connect(values.session);
  try {
    const source = await fs.readFile(new URL('./browser-reader.js', import.meta.url), 'utf8');
    const data = await browser.evaluate('(' + source + ')(' + JSON.stringify(config) + ')');
    const audits = values['audit-manifest'] ? await attachAudits(browser, values['audit-manifest'], values.exiftool, data.records) : [];
    const snapshot = {schema_version: 1, provider: 'google_photos', run_id: crypto.randomUUID(), collected_at: new Date().toISOString(),
      window: {start: config.start, end: config.end, query_start: config.queryDays[0], query_end: config.queryDays.at(-1), policy: 'utc-or-recorded-offset-v1'}, ...data, audits};
    process.stdout.write(JSON.stringify(snapshot) + '\n');
    console.error(JSON.stringify({records: data.records.length, unresolved: data.unresolved.length, audited_originals: audits.length, images_fetched: 0}));
  } finally {browser.close();}
}
if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) main().catch(() => {console.error('Photo metadata collection failed; no complete snapshot published. Check the local browser/session and bounded arguments.');process.exitCode = 1;});
