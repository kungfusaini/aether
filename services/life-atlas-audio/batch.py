#!/usr/bin/env python3
"""Explicit three-log full transcription batch; no scheduling or cloud editing."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid
import trial
import glossary

NAMES = ['log001.wav', 'log002.wav', 'log003.wav']
MAX_SECONDS = 3600
DEADLINE_SECONDS = 7200


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def persist_artifacts(receipt, boundary):
    output = Path(receipt['output_directory'])
    require(output.resolve().is_relative_to(boundary.resolve()) and not output.is_symlink(), 'Unsafe artifact persistence root')
    files = list(output.iterdir())
    log = output.parent/'engine-private.log'
    if log.exists(): files.append(log)
    directories = {boundary}
    for file in files:
        require(file.is_file() and not file.is_symlink(), 'Unsafe retained artifact')
        with file.open('rb') as src: os.fsync(src.fileno())
        directory = file.parent
        while directory.is_relative_to(boundary):
            directories.add(directory)
            if directory == boundary: break
            directory = directory.parent
    for directory in sorted(directories, key=lambda p:len(p.parts), reverse=True):
        sync_directory(directory)


def atomic_json(path, value):
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temp.open('x') as out:
            os.fchmod(out.fileno(), 0o600)
            json.dump(value, out, indent=2)
            out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def load_manifest(path):
    path = Path(path)
    require(not path.is_symlink() and path.is_file() and path.stat().st_size < 32768, 'Unsafe batch manifest')
    data = json.loads(path.read_text())
    require(re.fullmatch(r'full-logs-\d{8}', data.get('batch_id', '')) is not None, 'Invalid batch ID')
    files = data.get('files', [])
    require([f.get('name') for f in files] == NAMES, 'Only the approved three logs are accepted')
    for item in files:
        require(re.fullmatch(r'[0-9a-f]{64}', item.get('sha256', '')) is not None, 'Invalid source identity')
        require(type(item.get('size')) is int and 0 < item['size'] <= 1024**3, 'Invalid source size')
        require(type(item.get('duration_seconds')) in (int, float) and 0 < item['duration_seconds'] <= MAX_SECONDS,
                'Source duration exceeds this batch scope')
    return data, trial.sha256(path)


def verified_source(stage, item):
    source = stage/item['name']
    require(not source.is_symlink() and source.is_file(), 'Source is not a regular staged file')
    require(source.stat().st_size == item['size'] and trial.sha256(source) == item['sha256'], 'Staged source integrity mismatch')
    return source


def full_engine_args(lock, inputs, outputs, name, batch_id, with_names=False):
    # Keep the pilot's 90-second source limits and 15-minute deadline unchanged.
    # Only this separate full-file runner gets the explicit two-hour deadline.
    args = trial.engine_args(lock, inputs, outputs, name)
    args[2:2] = ['--pull', 'never']
    index = args.index('-c')
    # Read private vocabulary inside the container, never interpolate it into
    # Docker's host arguments/inspect command or shell source. Native whisper
    # still receives its supported prompt argument inside the trusted container.
    prefix = ('name_prompt=$(cat /input/names-prompt.txt) || exit 65; '
              '[ -n "$name_prompt" ] || exit 65; '
              'set -- "$@" --prompt "$name_prompt" --carry-initial-prompt; '
              if with_names else '')
    args[index+1] = prefix + ('nice -n 10 timeout --signal=TERM --kill-after=5s 7200s "$@"; '
        'result=$?; cat /sys/fs/cgroup/memory.peak > /output/memory-peak.txt; exit "$result"')
    image_index = args.index(lock['image'])
    args[image_index:image_index] = ['--label', 'life-atlas.audio-batch=' + batch_id]
    return args


def verify_completed(receipt, item, lock):
    require(receipt['source_name'] == item['name'] and receipt['source_sha256'] == item['sha256'] and receipt['model_sha256'] == lock['model']['sha256']
            and receipt['engine_image'] == lock['image'] and abs(receipt['audio_seconds']-item['duration_seconds']) < 0.1, 'Completed file belongs to another source/model')
    output = Path(receipt['output_directory'])
    require(not output.is_symlink() and output.resolve().is_relative_to((trial.STATE/'transcription-batches').resolve()), 'Completed output is outside batch storage')
    for name, key in [('transcript.txt', 'transcript_sha256'), ('transcript.json', 'json_sha256')]:
        require(not (output/name).is_symlink() and (output/name).is_file() and trial.sha256(output/name) == receipt[key], 'Completed transcription artifact changed')
    return receipt


def decode_args(source, pcm):
    return ['nice', '-n', '10', 'ffmpeg', '-nostdin', '-v', 'error', '-xerror',
        '-threads', '1', '-i', str(source), '-map', '0:a:0', '-vn', '-sn', '-dn',
        '-map_metadata', '-1', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(pcm)]


def transcribe(item, source, stage, output_root, lock, batch_id, name_hints=None):
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    require(available >= 1800*1024*1024, 'Insufficient memory headroom; batch can be resumed')
    attempt = uuid.uuid4().hex
    inputs = stage/'converted'/Path(item['name']).stem/attempt
    outputs = output_root/Path(item['name']).stem/attempt/'output'
    trial.private_dir(inputs); trial.private_dir(outputs)
    hints = glossary.validate(name_hints if name_hints is not None else [])
    if hints:
        with (inputs/'names-prompt.txt').open('x') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(glossary.prompt(hints)); stream.flush(); os.fsync(stream.fileno())
    pcm = inputs/'input.wav'
    trial.checked(decode_args(source, pcm), timeout=300)
    info = json.loads(trial.checked(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(pcm)]))
    seconds = float(info['format']['duration'])
    require(abs(seconds-item['duration_seconds']) < 0.1, 'Decoded source duration mismatch')
    require(trial.sha256(source) == item['sha256'], 'Source changed during decoding')
    pcm_hash = trial.sha256(pcm)
    name = 'life-atlas-audio-trial-' + uuid.uuid4().hex
    started = time.monotonic()
    try:
        with (outputs.parent/'engine-private.log').open('xb') as log:
            result = subprocess.run(full_engine_args(lock, inputs, outputs, name, batch_id, with_names=bool(hints)),
                stdout=log, stderr=log, timeout=DEADLINE_SECONDS+30)
        require(result.returncode == 0, 'Full transcription failed; inspect only protected operational state')
    finally:
        trial.cleanup_container(name)
    text = (outputs/'transcript.txt').read_text()
    data = json.loads((outputs/'transcript.json').read_text())
    require(bool(text.strip()) and bool(data.get('transcription')), 'Full transcript is empty')
    offsets = [s.get('offsets', {}) for s in data['transcription']]
    require(all(type(x.get('from')) is int and type(x.get('to')) is int and 0 <= x['from'] <= x['to'] <= int(seconds*1000)+1000 for x in offsets),
            'Invalid full-recording timestamps')
    for file in outputs.iterdir():
        if file.is_file(): file.chmod(0o600)
    require(trial.sha256(source) == item['sha256'] and trial.sha256(pcm) == pcm_hash, 'Source/PCM changed during inference')
    return {'source_name':item['name'], 'source_sha256':item['sha256'],
        'source_size':item['size'], 'audio_seconds':seconds, 'model_sha256':lock['model']['sha256'],
        'engine_image':lock['image'], 'elapsed_seconds':round(time.monotonic()-started, 2),
        'peak_memory_bytes':int((outputs/'memory-peak.txt').read_text()),
        'output_directory':str(outputs), 'pcm_path':str(pcm), 'pcm_sha256':pcm_hash,
        'transcript_sha256':trial.sha256(outputs/'transcript.txt'),
        'json_sha256':trial.sha256(outputs/'transcript.json'),
        'transcript_characters':len(text), 'segments':len(data['transcription']),
        'names_prompt_sha256':trial.sha256(inputs/'names-prompt.txt') if hints else None,
        'completed_at':now()}


def run_batch(manifest_path):
    manifest, manifest_hash = load_manifest(manifest_path)
    lock = trial.read_lock()
    stage = trial.STATE/'staging'/manifest['batch_id']
    require(stage.is_dir() and not stage.is_symlink() and stage.stat().st_mode&0o077 == 0, 'Unsafe staging directory')
    model = trial.STATE/'models'/lock['model']['filename']
    require(trial.sha256(model) == lock['model']['sha256'], 'Model identity mismatch')
    # Reject incomplete/changed staging before publishing inference intent.
    for item in manifest['files']:
        verified_source(stage, item)
    root = trial.STATE/'transcription-batches'
    trial.private_dir(root)
    sync_directory(root.parent)
    with (root/'worker.lock').open('a') as gate, trial.engine_guard():
        try: fcntl.flock(gate, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Another full batch is active')
        output = root/manifest['batch_id']; trial.private_dir(output)
        sync_directory(output.parent)
        status_path = output/'status.json'
        if status_path.exists():
            status = json.loads(status_path.read_text())
            require(status['manifest_sha256'] == manifest_hash and status['lock_sha256'] == trial.sha256(trial.SOURCE/'trial.lock.json'), 'Batch inputs/config changed; refuse unsafe resume')
        else:
            status = {'batch_id':manifest['batch_id'], 'manifest_sha256':manifest_hash,
                'lock_sha256':trial.sha256(trial.SOURCE/'trial.lock.json'),
                'started_at':now(), 'files':{}, 'status':'preparing'}
            atomic_json(output/'source-manifest.json', manifest)
        try:
            for item in manifest['files']:
                source = verified_source(stage, item)
                receipt_path = output/(Path(item['name']).stem+'-receipt.json')
                if receipt_path.exists():
                    receipt = verify_completed(json.loads(receipt_path.read_text()), item, lock)
                    status['files'][item['name']] = receipt
                    continue
                status.update(status='transcribing', current_file=item['name'], updated_at=now())
                atomic_json(status_path, status)
                receipt = transcribe(item, source, stage, output, lock, manifest['batch_id'])
                persist_artifacts(receipt, output)
                atomic_json(receipt_path, receipt)
                status['files'][item['name']] = receipt
                status.update(updated_at=now(), current_file=None)
                atomic_json(status_path, status)
            status.update(status='transcribed-awaiting-editorial-review', current_file=None, updated_at=now())
            atomic_json(status_path, status)
            print(json.dumps({'batch_id':manifest['batch_id'], 'status':status['status'], 'files':len(status['files'])}))
        except BaseException as error:
            status.update(status='interrupted' if isinstance(error, (KeyboardInterrupt, InterruptedError)) else 'failed',
                          updated_at=now(), error_type=type(error).__name__,
                          reason=str(error) if type(error) is RuntimeError else 'Operation failed; private diagnostics suppressed')
            atomic_json(status_path, status)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    args = parser.parse_args()
    def stop(*_): raise InterruptedError('Batch stop requested')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    run_batch(args.manifest)


if __name__ == '__main__':
    os.umask(0o077)
    try: main()
    except Exception as error:
        print('Full audio batch failed:',type(error).__name__,'(private diagnostics suppressed)',file=sys.stderr)
        raise SystemExit(1)
