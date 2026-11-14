#!/usr/bin/env python3
"""Assemble approved, reviewed logs into an atomic daily transcript/audio pair.

No ingestion, inference, cloud calls, source deletion or automatic backfill.
Runtime manifests and outputs must be private and outside Git.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid
import zipfile

import batch
import review
import trial


def require(condition, message):
    if not condition:
        raise ValueError(message)


def private_root(path):
    path = Path(path).absolute()
    require(not path.is_symlink(), 'Symlink root rejected')
    require(not any((p/'.git').exists() for p in [path, *path.parents]), 'Private data cannot live in Git')
    require(path.resolve() == path, 'Symlink ancestors rejected')
    require(path.is_dir() and path.stat().st_uid == os.geteuid() and not path.stat().st_mode & 0o077,
            'Existing caller-owned private root required')
    return path


def checked_file(root, relative, expected):
    require(isinstance(relative, str) and relative and not Path(relative).is_absolute(), 'Relative file path required')
    path = root/relative
    require(path.resolve().is_relative_to(root) and path.resolve() == path and path.is_file(), 'Unsafe input path')
    require(path.stat().st_uid == os.geteuid() and not path.stat().st_mode & 0o077, 'Input is not private')
    require(re.fullmatch('[0-9a-f]{64}', expected or '') is not None and trial.sha256(path) == expected,
            'Input hash mismatch')
    return path


def validate_manifest(manifest):
    require(isinstance(manifest, dict) and set(manifest) == {'schema', 'day', 'logs'}, 'Invalid daily manifest fields')
    require(manifest['schema'] == 1 and isinstance(manifest['day'], str), 'Invalid daily manifest schema')
    day = dt.datetime.strptime(manifest['day'], '%Y%m%d').strftime('%Y%m%d')
    require(day == manifest['day'], 'Invalid daily date')
    logs = manifest['logs']
    require(isinstance(logs, list) and 0 < len(logs) <= 100, 'Expected 1-100 selected recordings')
    ids = set()
    for log in logs:
        require(set(log) == {'source_sha256', 'recorded_at', 'date_basis', 'raw', 'proposal', 'opus'}, 'Invalid log fields')
        identity = log['source_sha256']
        require(isinstance(identity, str) and re.fullmatch('[0-9a-f]{64}', identity) is not None and identity not in ids,
                'Invalid or duplicate source identity')
        ids.add(identity)
        recorded = dt.datetime.fromisoformat(log['recorded_at'])
        require(recorded.utcoffset() is not None and recorded.strftime('%Y%m%d') == day, 'Recording must belong to its local day')
        require(log['date_basis'] in {'phone-recording-metadata', 'user-confirmed'}, 'Unverified recording date')
        for name in ('raw', 'proposal', 'opus'):
            require(isinstance(log[name], dict) and set(log[name]) == {'path', 'sha256'}, 'Invalid artifact reference')
    return sorted(logs, key=lambda item: (dt.datetime.fromisoformat(item['recorded_at']), item['source_sha256']))


def verify_opus(path):
    data = json.loads(trial.checked(['ffprobe', '-v', 'error', '-select_streams', 'a',
        '-show_entries', 'stream=codec_name,channels:format=duration', '-of', 'json', str(path)]))
    streams = data.get('streams', [])
    require(len(streams) == 1 and streams[0]['codec_name'] == 'opus' and
            streams[0]['channels'] == 1 and 0 < float(data['format']['duration']) <= 3600.1,
            'Expected a bounded mono Opus recording')
    trial.checked(['nice', '-n', '10', 'ffmpeg', '-nostdin', '-v', 'error', '-xerror',
        '-threads', '1', '-i', str(path), '-map', '0:a:0', '-threads', '1', '-f', 'null', '-'], timeout=900)


def prepare(manifest, inputs):
    logs = validate_manifest(manifest)
    bodies, audio, retained = [], [], []
    for index, log in enumerate(logs, 1):
        files = {name: checked_file(inputs, item['path'], item['sha256'])
                 for name, item in log.items() if name in {'raw', 'proposal', 'opus'}}
        raw, raw_hash = review.read_json(files['raw'])
        proposal, proposal_hash = review.read_json(files['proposal'])
        require(raw_hash == log['raw']['sha256'] and proposal_hash == log['proposal']['sha256'],
                'Review inputs changed during reading')
        draft, _ = review.apply_proposal(raw, raw_hash, proposal)
        # Existing renderer fences all speech as literal text; never interpolate
        # speech or model output into Markdown headings or executable markup.
        bodies.append('## Recording ' + str(index) + ' — ' + log['recorded_at'] + '\n\n' + draft)
        opus = files['opus']
        require(opus.suffix.lower() == '.opus' and opus.stat().st_size <= 128*1024*1024, 'Expected bounded Opus audio')
        verify_opus(opus)
        archive_name = f'{index:03d}-' + log['source_sha256'] + '.opus'
        audio.append((archive_name, opus, log['opus']['sha256']))
        retained.append({'source_sha256': log['source_sha256'], 'recorded_at': log['recorded_at'],
                         'date_basis': log['date_basis'], 'audio_name': archive_name,
                         'raw_sha256': raw_hash, 'proposal_sha256': log['proposal']['sha256'],
                         'opus_sha256': log['opus']['sha256']})
    text = '# ' + manifest['day'] + ' — Audio logs\n\n'
    text += 'Edited reading drafts, not verified verbatim. Bracketed guesses remain uncertain. No summary.\n\n'
    text += '\n'.join(bodies)
    return text.encode(), audio, retained


def bundle(manifest, inputs, output):
    inputs, output = private_root(inputs), private_root(output)
    # Validate *all* source identities before publishing anything.
    text, audio, retained = prepare(manifest, inputs)
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    day = manifest['day']
    revisions = output/'.revisions'
    trial.private_dir(revisions)
    private_root(revisions)
    day_revisions = revisions/day
    trial.private_dir(day_revisions)
    private_root(day_revisions)
    batch.sync_directory(day_revisions)
    batch.sync_directory(revisions)
    batch.sync_directory(output)
    lock_path = output/'.publish.lock'
    require(not lock_path.is_symlink(), 'Unsafe publisher lock')
    with lock_path.open('a') as gate:
        os.fchmod(gate.fileno(), 0o600)
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = output/day
        require(not os.path.lexists(current) or (current.is_symlink() and current.resolve().parent == day_revisions),
                'Refuse to replace an unrelated daily directory')
        if current.is_symlink():
            old = json.loads((current/'manifest.json').read_text())
            old_sources = {item['source_sha256'] for item in old['logs']}
            require(old_sources <= {item['source_sha256'] for item in retained}, 'Daily update cannot drop previous logs')
        final = day_revisions/identity
        if final.exists():
            require(not final.is_symlink(), 'Unsafe revision')
            receipt = json.loads((final/'manifest.json').read_text())
            require(receipt['input_manifest_sha256'] == identity and receipt['logs'] == retained, 'Existing revision mismatch')
            for name in ('transcript.md', 'audio.zip'):
                checked_file(output, str((final/name).relative_to(output)), receipt['files'][name])
            require((final/'transcript.md').read_bytes() == text, 'Existing transcript differs')
        else:
            # Retain failed attempts privately for diagnosis; never overwrite them.
            attempt = day_revisions/('.building-' + uuid.uuid4().hex)
            attempt.mkdir(mode=0o700)
            (attempt/'transcript.md').write_bytes(text)
            (attempt/'transcript.md').chmod(0o600)
            with zipfile.ZipFile(attempt/'audio.zip', 'x', compression=zipfile.ZIP_STORED) as archive:
                for name, path, _ in audio:
                    archive.write(path, name)
            (attempt/'audio.zip').chmod(0o600)
            with zipfile.ZipFile(attempt/'audio.zip') as archive:
                require(archive.testzip() is None and archive.namelist() == [name for name, _, _ in audio], 'Invalid audio bundle')
                for name, _, expected in audio:
                    with archive.open(name) as src:
                        digest = hashlib.file_digest(src, 'sha256').hexdigest()
                    require(digest == expected, 'Audio bundle recovery mismatch')
            # Detect any mutation during assembly, including changed proposals/raw.
            require(prepare(manifest, inputs)[0] == text, 'Source changed while assembling')
            receipt = {'schema': 1, 'day': day, 'input_manifest_sha256': identity, 'logs': retained,
                       'files': {name: trial.sha256(attempt/name) for name in ('transcript.md', 'audio.zip')},
                       'verified_verbatim': False, 'backup_verified': False}
            batch.atomic_json(attempt/'manifest.json', receipt)
            for path in attempt.iterdir():
                with path.open('rb') as src:
                    os.fsync(src.fileno())
            batch.sync_directory(attempt)
            os.rename(attempt, final)
            batch.sync_directory(day_revisions)
        # One pointer replacement selects a complete revision. Readers needing
        # both files must resolve_day() ONCE and open both through that path.
        # Old immutable revisions remain available for backups/recovery.
        pointer = output/('.pointer-' + uuid.uuid4().hex)
        pointer.symlink_to(final.relative_to(output), target_is_directory=True)
        try:
            os.replace(pointer, current)
            batch.sync_directory(output)
        finally:
            pointer.unlink(missing_ok=True)
    return {'day': day, 'logs': len(retained), 'revision': identity, 'backup_verified': False}


def resolve_day(output, day):
    """Pin one revision before reading both files; do not resolve per file."""
    output = private_root(output)
    require(isinstance(day, str) and re.fullmatch(r'\d{8}', day) is not None, 'Invalid day')
    current = output/day
    require(current.is_symlink(), 'Daily revision is not published')
    revision = current.resolve(strict=True)
    require(revision.parent == output/'.revisions'/day and re.fullmatch('[0-9a-f]{64}', revision.name) is not None,
            'Unsafe published revision')
    private_root(revision)
    receipt = json.loads((revision/'manifest.json').read_text())
    require(receipt['day'] == day and receipt['input_manifest_sha256'] == revision.name, 'Published revision mismatch')
    for name in ('transcript.md', 'audio.zip'):
        checked_file(output, str((revision/name).relative_to(output)), receipt['files'][name])
    return revision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--input-root', required=True)
    parser.add_argument('--output-root', required=True)
    args = parser.parse_args()
    inputs = private_root(args.input_root)
    path = Path(args.manifest).absolute()
    require(path.is_relative_to(inputs), 'Manifest must be inside private input root')
    checked_file(inputs, str(path.relative_to(inputs)), trial.sha256(path))
    manifest, _ = review.read_json(path)
    print(json.dumps(bundle(manifest, inputs, args.output_root)))


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except Exception as error:
        print('Daily assembly failed:', type(error).__name__, '(private diagnostics suppressed)', file=sys.stderr)
        raise SystemExit(1)
