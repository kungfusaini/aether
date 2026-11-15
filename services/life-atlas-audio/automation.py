#!/usr/bin/env python3
"""Receipt-driven selected audio intake and review queue; never print speech."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import uuid
from zoneinfo import ZoneInfo
import batch
import daily
import review
import trial
import glossary

ROOT = trial.STATE
CONFIG = Path('/etc/life-atlas-audio/automation.json')
NAMES_FILE = Path('/etc/life-atlas-audio/names.json')
MODEL_PROVIDER = 'openai-codex'
MODEL_ID = 'gpt-6.1-sol'
EXTENSIONS = {'wav', 'm4a', 'aac', 'mp3', 'ogg', 'opus', 'flac', 'amr', '3gp', 'mp4', 'aiff', 'aif'}


def require(value):
    if not value:
        raise RuntimeError('Audio automation safety check failed; private diagnostics suppressed')


def config():
    data = json.loads(CONFIG.read_text())
    require(data['schema'] == 1 and data['enabled'] is True)
    ZoneInfo(data['timezone'])
    require(dt.datetime.fromisoformat(data['not_before']).utcoffset() is not None)
    return data


def control():
    root = ROOT/'automation'
    trial.private_dir(root)
    trial.private_dir(root/'jobs')
    trial.private_dir(root/'health')
    return root


def job_path(identity):
    require(isinstance(identity, str) and re.fullmatch('[0-9a-f]{64}', identity) is not None)
    path = control()/'jobs'/identity
    require(not path.is_symlink())
    return path


def allowed_name(name, cfg):
    return bool(re.fullmatch(r'log[a-z0-9_. -]*\.[a-z0-9]+', name, re.I) and
                name.rsplit('.', 1)[-1].lower() in EXTENSIONS and
                name.lower() not in {n.lower() for n in cfg['excluded_names']})


def probe(source):
    data = json.loads(trial.checked(['ffprobe', '-v', 'error', '-select_streams', 'a',
        '-show_entries', 'stream=codec_name,channels:format=duration', '-of', 'json', str(source)]))
    require(len(data['streams']) == 1)
    duration = float(data['format']['duration'])
    require(0 < duration <= 3600)
    return duration


def encode(source, dest, duration):
    part = dest.with_name('.audio-'+uuid.uuid4().hex+'.opus')
    trial.checked(['nice', '-n', '10', 'ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '1',
        '-i', str(source), '-map', '0:a:0', '-vn', '-sn', '-dn', '-map_metadata', '-1',
        '-ac', '1', '-ar', '48000', '-c:a', 'libopus', '-b:a', '24k', '-vbr', 'on',
        '-application', 'audio', '-compression_level', '10', '-threads', '1', str(part)], timeout=900)
    part.chmod(0o600)
    daily.verify_opus(part)
    require(abs(probe(part)-duration) < 0.1)
    with part.open('rb') as src: os.fsync(src.fileno())
    os.rename(part, dest); batch.sync_directory(dest.parent)


def save(path, value):
    batch.atomic_json(path, value)


def inspect_source(path, cfg):
    require(path.parent == ROOT/'incoming' and allowed_name(path.name, cfg) and not path.is_symlink() and path.is_file())
    before = path.stat()
    require(0 < before.st_size <= 1024**3)
    # Syncthing publishes completed files by rename; its temporary names never
    # pass the root-level log-name allowlist. Also require an unchanged quiet file.
    require(dt.datetime.now().timestamp()-before.st_mtime >= 30)
    identity = trial.sha256(path)
    after = path.stat()
    require((before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns))
    return identity, before


def process_source(path, cfg):
    identity, source_stat = inspect_source(path, cfg)
    job = job_path(identity); trial.private_dir(job)
    info_path = job/'source.json'
    if info_path.exists():
        info = json.loads(info_path.read_text())
        require(info['source_sha256'] == identity and info['source_size'] == source_stat.st_size)
    else:
        stamp = dt.datetime.fromtimestamp(source_stat.st_mtime, ZoneInfo(cfg['timezone']))
        info = {'source_name': path.name, 'source_sha256': identity, 'source_size': source_stat.st_size,
            'source_mtime_ns': source_stat.st_mtime_ns, 'recorded_at': stamp.isoformat(),
            'day': stamp.strftime('%Y%m%d'), 'date_basis': 'phone-recording-metadata',
            'timestamp_basis': 'Syncthing-preserved phone recording file save time, not exact speech start',
            'timezone': cfg['timezone'], 'timezone_basis': cfg['timezone_basis']}
        # Freeze vocabulary only for newly created source jobs. Existing jobs
        # retain their original context; a later glossary edit is not a rerun.
        if not (job/'names.json').exists():
            save(job/'names.json', glossary.read(NAMES_FILE, missing_ok=True))
        glossary.read(job/'names.json')
        info['names_sha256'] = trial.sha256(job/'names.json')
        save(info_path, info)
    if (job/'asr-receipt.json').exists() and (job/'audio-receipt.json').exists():
        validate_job(job)
        return identity
    require(shutil.disk_usage(ROOT).free >= 10_000_000_000)
    stage = ROOT/'staging'/('auto-'+identity); trial.private_dir(stage)
    source = stage/path.name
    if not source.exists():
        part = stage/('.upload-'+uuid.uuid4().hex)
        with path.open('rb') as src, part.open('xb') as out:
            os.fchmod(out.fileno(), 0o600)
            shutil.copyfileobj(src, out, 1024*1024); out.flush(); os.fsync(out.fileno())
        require(trial.sha256(part) == identity and trial.sha256(path) == identity)
        os.rename(part, source); batch.sync_directory(stage)
    require(not source.is_symlink() and trial.sha256(source) == identity)
    duration = probe(source)
    item = {'name': path.name, 'sha256': identity, 'size': source_stat.st_size, 'duration_seconds': duration}
    output = ROOT/'transcription-batches'/('auto-'+identity); trial.private_dir(output)
    hints = frozen_names(job)
    lock = trial.read_lock()
    require(trial.sha256(trial.STATE/'models'/lock['model']['filename']) == lock['model']['sha256'])
    if (job/'asr-receipt.json').exists():
        receipt = batch.verify_completed(json.loads((job/'asr-receipt.json').read_text()), item, lock)
        require(receipt.get('names_sha256') == info.get('names_sha256'))
    else:
        available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
        require(available >= 1800*1024*1024)
        with trial.engine_guard():
            receipt = batch.transcribe(item, source, stage, output, lock, 'auto-'+identity, name_hints=hints or [])
            receipt['names_sha256'] = info.get('names_sha256')
            batch.persist_artifacts(receipt, output)
            save(job/'asr-receipt.json', receipt)
    # Immutable copies for queue/backup; original raw ASR remains untouched.
    raw = job/'raw.json'
    if not raw.exists():
        data = Path(receipt['output_directory'])/'transcript.json'
        require(trial.sha256(data) == receipt['json_sha256'])
        with raw.open('xb') as dst:
            os.fchmod(dst.fileno(), 0o600); dst.write(data.read_bytes()); dst.flush(); os.fsync(dst.fileno())
    require(trial.sha256(raw) == receipt['json_sha256'])
    raw_text = job/'raw.txt'
    if not raw_text.exists():
        original = Path(receipt['output_directory'])/'transcript.txt'
        require(trial.sha256(original) == receipt['transcript_sha256'])
        with raw_text.open('xb') as dst:
            os.fchmod(dst.fileno(), 0o600); dst.write(original.read_bytes()); dst.flush(); os.fsync(dst.fileno())
    require(trial.sha256(raw_text) == receipt['transcript_sha256'])
    audio = job/'audio.opus'
    if not (job/'audio-receipt.json').exists():
        if audio.exists():
            # Preserve a completed-but-unreceipted attempt, never assume it valid.
            os.rename(audio, job/('unreceipted-'+uuid.uuid4().hex+'.opus'))
        encode(source, audio, duration)
        save(job/'audio-receipt.json', {'sha256': trial.sha256(audio), 'duration_seconds': duration})
    validate_job(job)
    save(job/'status.json', {'status': 'awaiting-editor', 'source_sha256': identity, 'updated_at': batch.now()})
    return identity


def frozen_names(job):
    info = json.loads((job/'source.json').read_text())
    if 'names_sha256' not in info:
        return None  # Pre-feature job: do not consult mutable current vocabulary.
    require(trial.sha256(job/'names.json') == info['names_sha256'])
    return glossary.read(job/'names.json')['names']


def validate_job(job):
    asr = json.loads((job/'asr-receipt.json').read_text())
    audio = json.loads((job/'audio-receipt.json').read_text())
    require(asr['source_sha256'] == job.name and trial.sha256(job/'raw.json') == asr['json_sha256'] and
            trial.sha256(job/'raw.txt') == asr['transcript_sha256'] and trial.sha256(job/'audio.opus') == audio['sha256'])
    info = json.loads((job/'source.json').read_text())
    hints = frozen_names(job)
    require(asr.get('names_sha256') == info.get('names_sha256'))
    if hints is not None:
        expected_prompt = hashlib.sha256(glossary.prompt(hints).encode()).hexdigest() if hints else None
        require(asr.get('names_prompt_sha256') == expected_prompt)
    return asr, audio


def scan():
    cfg = config()
    if dt.datetime.now(dt.timezone.utc) < dt.datetime.fromisoformat(cfg['not_before']):
        return {'status': 'waiting-for-first-scheduled-window', 'processed': 0}
    root = control()
    with (root/'worker.lock').open('a') as gate:
        try: fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return {'status': 'busy', 'processed': 0}
        count = 0; failures = []; blocked = False
        for path in sorted((ROOT/'incoming').iterdir()):
            if allowed_name(path.name, cfg) and path.is_file() and not path.is_symlink():
                try:
                    process_source(path, cfg); count += 1
                except Exception as error:
                    failures.append({'source_name': path.name, 'error_type': type(error).__name__})
                    # An interrupted/uncertain Docker launch is a global safety
                    # barrier, not a source-specific validation failure.
                    if (trial.STATE/'transcription-control'/'launch-intent.json').exists():
                        blocked = True
                        break
        save(root/'health'/'source-errors.json', {'updated_at': batch.now(), 'failures': failures})
        result = {'status': 'blocked-inference' if blocked else ('partial' if failures else 'ok'),
                  'observed_selected': count, 'failed_sources': len(failures), 'updated_at': batch.now(),
                  'incoming_raw_retained': True, 'independent_backup': False}
        save(root/'health'/'intake.json', result)
        return result


def pending():
    result = []
    for job in sorted((control()/'jobs').iterdir()):
        if not job.is_dir() or job.is_symlink() or not re.fullmatch('[0-9a-f]{64}', job.name): continue
        if (job/'asr-receipt.json').exists() and (job/'audio-receipt.json').exists():
            validate_job(job)
            if not (job/'completion.json').exists():
                result.append({'id': job.name, 'has_review': (job/'review-receipt.json').exists()})
    return result


def packet(identity):
    job = job_path(identity)
    validate_job(job)
    result = review.packet(job/'raw.json')
    hints = frozen_names(job)
    if hints is not None:
        result['name_hints'] = hints
    return result


def accept(identity, payload):
    with (control()/'publish.lock').open('a') as gate:
        fcntl.flock(gate, fcntl.LOCK_EX)
        return accept_locked(identity, payload)


def accept_locked(identity, payload):
    job = job_path(identity)
    validate_job(job)
    require(set(payload) == {'proposal', 'receipt'})
    proposal, receipt = payload['proposal'], payload['receipt']
    require(receipt['provider'] == MODEL_PROVIDER and receipt['model'] == MODEL_ID and
            receipt['status'] == 'proposed-awaiting-server-validation' and receipt['audio_shared'] is False)
    raw, raw_hash = review.read_json(job/'raw.json')
    hints = frozen_names(job)
    if hints is not None:
        require(receipt.get('name_hints_sha256') == hashlib.sha256(json.dumps(hints, ensure_ascii=False).encode()).hexdigest())
    require(receipt['source_sha256'] == raw_hash and receipt['packet_sha256'] == hashlib.sha256(
        json.dumps(packet(identity), ensure_ascii=False).encode()).hexdigest())
    # Bridge sends exact proposal bytes separately hash-bound to its receipt.
    proposal_bytes = payload['proposal'].encode()
    require(hashlib.sha256(proposal_bytes).hexdigest() == receipt['proposal_sha256'])
    proposal = json.loads(proposal_bytes)
    try:
        draft, ledger = review.apply_proposal(raw, raw_hash, proposal)
    except ValueError:
        # An explicit deterministic rejection permits bounded regeneration.
        # Operational/transport failures remain ambiguous and must reuse the
        # existing proposal rather than spend another model call.
        return {'status': 'rejected-proposal', 'source_sha256': identity}
    if (job/'review-receipt.json').exists():
        require(json.loads((job/'proposal.json').read_text()) == proposal)
        return {'status': 'already-reviewed'}
    save(job/'proposal.json', proposal)
    save(job/'edit-ledger.json', ledger)
    save(job/'editor-receipt.json', receipt)
    draft_path = job/'reading-draft.md'
    with draft_path.open('w') as dst:
        os.fchmod(dst.fileno(), 0o600); dst.write(draft); dst.flush(); os.fsync(dst.fileno())
    save(job/'review-receipt.json', {'raw_sha256': raw_hash, 'proposal_sha256': trial.sha256(job/'proposal.json'),
        'draft_sha256': trial.sha256(draft_path), 'verified_verbatim': False})
    return {'status': 'reviewed'}


def restic(*args):
    env = os.environ | {'RESTIC_REPOSITORY': str(ROOT/'restic'),
        'RESTIC_PASSWORD_FILE': '/etc/life-atlas-audio/restic-password',
        'RESTIC_CACHE_DIR': '/var/cache/life-atlas-audio-restic', 'GOMAXPROCS': '1'}
    return trial.checked(['nice', '-n', '10', 'restic', *args], timeout=1800, env=env)


def finish(identity):
    job = job_path(identity)
    validate_job(job)
    root = control()
    with (root/'publish.lock').open('a') as gate:
        fcntl.flock(gate, fcntl.LOCK_EX)
        if (job/'completion.json').exists(): return json.loads((job/'completion.json').read_text())
        info = json.loads((job/'source.json').read_text())
        day = info['day']
        logs = []
        for other in sorted((root/'jobs').iterdir()):
            if not (other/'review-receipt.json').exists(): continue
            meta = json.loads((other/'source.json').read_text())
            if meta['day'] != day: continue
            validate_job(other)
            receipt = json.loads((other/'review-receipt.json').read_text())
            require(trial.sha256(other/'proposal.json') == receipt['proposal_sha256'] and
                    trial.sha256(other/'reading-draft.md') == receipt['draft_sha256'])
            ref = lambda name: {'path': str((other/name).relative_to(root)), 'sha256': trial.sha256(other/name)}
            logs.append({'source_sha256': other.name, 'recorded_at': meta['recorded_at'], 'date_basis': meta['date_basis'],
                         'raw': ref('raw.json'), 'proposal': ref('proposal.json'), 'opus': ref('audio.opus')})
        require(any(log['source_sha256'] == identity for log in logs))
        days = ROOT/'daily'; trial.private_dir(days)
        manifest = {'schema': 1, 'day': day, 'logs': logs}
        # ROOT is group-traversable for the isolated Syncthing account; only
        # the root-private automation subtree is a valid assembler input root.
        daily.bundle(manifest, root, days)
        revision = daily.resolve_day(days, day)
        # Preserve immutable raw ASR, edits, receipts and compressed audio, not
        # incoming/staging WAVs or converted PCM. Back up real revisions too.
        require(shutil.disk_usage(ROOT).free >= 10_000_000_000)
        backed = [root/'jobs', days/'.revisions']
        rows = [json.loads(line) for line in restic('backup', '--json', '--tag', 'daily-audio', *map(str, backed)).splitlines() if line.startswith(b'{')]
        sid = next(row['snapshot_id'] for row in rows if row.get('message_type') == 'summary')
        retained = [p for p in job.rglob('*') if p.is_file()] + [p for p in revision.iterdir() if p.is_file()]
        for path in retained:
            require(not path.is_symlink())
            require(hashlib.sha256(restic('dump', sid, str(path))).hexdigest() == trial.sha256(path))
        restic('check', '--read-data')
        # Leave incoming originals in place: deleting receive-only Syncthing
        # files would need a separate verified no-reupload design. Phone intact.
        # Remove only exact worker-owned stage/PCM after the verified snapshot.
        asr = json.loads((job/'asr-receipt.json').read_text())
        stage = ROOT/'staging'/('auto-'+identity)
        targets = [(stage/info['source_name'], identity), (Path(asr['pcm_path']), asr['pcm_sha256'])]
        if asr.get('names_prompt_sha256') is not None:
            targets.append((Path(asr['pcm_path']).with_name('names-prompt.txt'), asr['names_prompt_sha256']))
        for path, expected in targets:
            require(not path.is_symlink() and path.resolve().is_relative_to(stage))
            if path.exists():
                require(trial.sha256(path) == expected)
        for path, expected in targets:
            if path.exists():
                require(trial.sha256(path) == expected); path.unlink(); batch.sync_directory(path.parent)
        result = {'status': 'complete', 'source_sha256': identity, 'day': day, 'snapshot_id': sid,
            'revision': revision.name, 'backup_restore_verified': True, 'independent_backup': False,
            'incoming_raw_retained': True, 'phone_untouched': True, 'completed_at': batch.now()}
        save(job/'completion.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['scan', 'pending', 'packet', 'accept', 'finish'])
    parser.add_argument('identity', nargs='?')
    args = parser.parse_args()
    if args.action == 'scan': result = scan()
    elif args.action == 'pending': result = pending()
    elif args.action == 'packet': result = packet(args.identity)
    elif args.action == 'finish': result = finish(args.identity)
    else:
        data = sys.stdin.buffer.read(16*1024*1024+1)
        require(len(data) <= 16*1024*1024)
        result = accept(args.identity, json.loads(data))
    # packet is deliberately a private machine-to-machine response. Callers
    # must capture it to a private file, never dump it into chat/worklogs.
    print(json.dumps(result, ensure_ascii=False))
    if args.action == 'scan' and result['status'] in {'partial', 'blocked-inference'}:
        raise SystemExit(1)


if __name__ == '__main__':
    os.umask(0o077)
    def stop(*_): raise InterruptedError('Audio automation interrupted')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try: main()
    except Exception as error:
        print('Audio automation failed:', type(error).__name__, '(private diagnostics suppressed)', file=sys.stderr)
        raise SystemExit(1)
