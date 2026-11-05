import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const execute = promisify(execFile);

export function auditFlags(embedded) {
  const names = new Set(Object.keys(embedded).map(k => k.split(':').at(-1)));
  return {
    // Presence of the original-image date tag is evidence, not proof it is true.
    capture_date_present: names.has('DateTimeOriginal'),
    creation_or_digitization_date_present: ['CreateDate', 'DateTimeDigitized', 'CreationDate', 'DateCreated'].some(k => names.has(k)),
    timezone_present: ['OffsetTimeOriginal', 'OffsetTimeDigitized', 'OffsetTime', 'TimeZoneOffset'].some(k => names.has(k)),
    gps_present: names.has('GPSLatitude') && names.has('GPSLongitude'),
    camera_present: names.has('Make') || names.has('Model'),
  };
}

export async function inspectOriginalBytes(originals, exiftool = 'exiftool') {
  if (!originals.length || originals.length > 10) throw Error('Original audit is limited to ten existing files');
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'life-atlas-exif-'));
  await fs.chmod(dir, 0o700);
  try {
    const files = [];
    for (const [i, original] of originals.entries()) {
      if (!Buffer.isBuffer(original.bytes) || original.bytes.length > 32 * 1024 * 1024) throw Error('Invalid original audit bytes');
      const file = path.join(dir, i + '.image');
      await fs.writeFile(file, original.bytes, {mode: 0o600}); files.push(file);
    }
    // Freshly inspect the verified bytes, never an unrelated/stale JSON report.
    const {stdout} = await execute(exiftool, ['-json', '-G1', '-a', '-s', '-n', ...files], {maxBuffer: 16 * 1024 * 1024, timeout: 60000});
    const reports = JSON.parse(stdout);
    if (!Array.isArray(reports) || reports.length !== files.length) throw Error('Incomplete original metadata inspection');
    const result = [];
    for (const [i, report] of reports.entries()) {
      if (report.SourceFile !== files[i] || report['ExifTool:Error'] || report.Error) throw Error('Original metadata report identity/error');
      const hash = value => crypto.createHash('sha256').update(value).digest('hex');
      if (hash(await fs.readFile(files[i])) !== hash(originals[i].bytes)) throw Error('Metadata tool modified its input');
      const embedded = Object.fromEntries(Object.entries(report).filter(([k]) => !['SourceFile', 'System', 'File', 'ExifTool', 'Composite'].includes(k.split(':')[0])));
      result.push({embedded_tags: embedded, ...auditFlags(embedded)});
    }
    return result;
  } finally {await fs.rm(dir, {recursive: true, force: true});}
}
