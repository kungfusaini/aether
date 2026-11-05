import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {auditFlags, inspectOriginalBytes} from './audit.mjs';
assert.equal(auditFlags({'ExifIFD:CreateDate': '2024:01:01 12:00:00'}).capture_date_present, false);
assert.equal(auditFlags({'ExifIFD:CreateDate': '2024:01:01 12:00:00'}).creation_or_digitization_date_present, true);
assert.equal(auditFlags({'ExifIFD:DateTimeDigitized': '2024:01:01 12:00:00'}).capture_date_present, false);
assert.equal(auditFlags({'ExifIFD:DateTimeOriginal': '2024:01:01 12:00:00'}).capture_date_present, true);
assert.equal(auditFlags({'GPS:GPSVersionID': '2 3 0 0'}).gps_present, false);
assert.equal(auditFlags({'GPS:GPSLatitude': 1, 'GPS:GPSLongitude': 2}).gps_present, true);
const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'photo-audit-test-'));
try {
 const fake = path.join(dir, 'exiftool');
 await fs.writeFile(fake, '#!' + process.execPath + '\n' + `const fs=require('fs');const files=process.argv.slice(2).filter(x=>!x.startsWith('-'));console.log(JSON.stringify(files.map(file=>({SourceFile:file,...(fs.readFileSync(file,'utf8')==='creation-only'?{'ExifIFD:CreateDate':'2024:01:01 12:00:00'}:{'ExifIFD:DateTimeOriginal':'2024:01:01 12:00:00'})}))));`, {mode: 0o700});
 // A stale same-basename report is deliberately present. There is no API to
 // consume it: evidence must come from a fresh tool invocation on verified bytes.
 await fs.writeFile(path.join(dir, '0.image.json'), JSON.stringify({SourceFile:'0.image', 'ExifIFD:DateTimeOriginal':'1999:01:01 00:00:00'}));
 const originals = [{bytes: Buffer.from('creation-only')}, {bytes: Buffer.from('capture')}];
 const result = await inspectOriginalBytes(originals, fake);
 assert.equal(result[0].capture_date_present, false);assert.equal(result[0].creation_or_digitization_date_present, true);assert.equal(result[1].capture_date_present, true);
 assert.ok(!JSON.stringify(result).includes('1999'));assert.ok(!JSON.stringify(result).includes('SourceFile'));assert.equal(originals[0].bytes.toString(), 'creation-only');
 await assert.rejects(inspectOriginalBytes([], fake));await assert.rejects(inspectOriginalBytes(Array(11).fill(originals[0]), fake));
 const bad = path.join(dir,'bad-tool');await fs.writeFile(bad,'#!'+process.execPath+'\nconsole.log(JSON.stringify([{SourceFile:"another-file"}]))',{mode:0o700});
 await assert.rejects(inspectOriginalBytes([originals[0]],bad), /identity/);
 console.log('Original audit: verified-byte inspection, stale-report resistance, tag semantics, GPS coordinates, sample bounds and identity checks passed (synthetic only).');
} finally {await fs.rm(dir, {recursive: true, force: true});}
