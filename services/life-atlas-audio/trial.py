#!/usr/bin/env python3
"""Explicit quality trials only; private content never goes to stdout or the network."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.request
import uuid

SOURCE = Path(__file__).resolve().parent
STATE = Path('/srv/audio-archive')


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as src:
        for block in iter(lambda: src.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_lock():
    lock = json.loads((SOURCE / 'trial.lock.json').read_text())
    if not re.fullmatch(r'ghcr.io/ggml-org/whisper.cpp@sha256:[0-9a-f]{64}', lock['image']):
        raise RuntimeError('An immutable image digest is required')
    return lock


def checked(command, timeout=120, **kwargs):
    result = subprocess.run(command, timeout=timeout, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError('Operational subprocess failed; content and diagnostics suppressed')
    return result.stdout


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077:
        raise RuntimeError('Unsafe private state directory')


def download(asset, directory):
    private_dir(directory)
    dest = directory / asset['filename']
    if dest.exists():
        if not dest.is_file() or dest.is_symlink() or sha256(dest) != asset['sha256']:
            raise RuntimeError('Existing asset does not match the pinned hash')
        return dest
    fd, name = tempfile.mkstemp(prefix='.download-', dir=directory)
    part = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out, urllib.request.urlopen(asset['url'], timeout=120) as response:
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > asset['bytes']:
                    raise RuntimeError('Download exceeds pinned asset size')
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if total != asset['bytes'] or sha256(part) != asset['sha256']:
            raise RuntimeError('Downloaded asset hash/size mismatch')
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return dest


def install(lock):
    # No Docker login or credential enumeration. Anonymous public-image pull uses
    # a task-owned temporary client config; existing registry credentials untouched.
    with tempfile.TemporaryDirectory(prefix='audio-trial-docker-') as config:
        checked(['docker', '--config', config, 'pull', '--platform', lock['image_platform'], lock['image']], timeout=900)
    download(lock['model'], STATE / 'models')
    download(lock['public_fixture'], STATE / 'public-fixtures')
    print('Pinned ARM64 image and hash-verified English model/public sample installed')


def deadline_script(seconds):
    if not 0 < seconds <= 900:
        raise RuntimeError('Invalid container deadline')
    return ('timeout --signal=TERM --kill-after=5s ' + str(seconds) + 's "$@"; '
        'result=$?; cat /sys/fs/cgroup/memory.peak > /output/memory-peak.txt; exit "$result"')


def cleanup_container(name):
    if not re.fullmatch(r'life-atlas-audio-trial-[0-9a-f]{32}', name):
        raise RuntimeError('Unsafe cleanup target')
    probe = ['docker', 'ps', '-a', '--filter', 'name=^/' + name + '$',
        '--format', '{{.Names}}|{{.Label "life-atlas.audio-trial"}}']
    existing = checked(probe, timeout=15).decode().strip()
    if existing:
        if existing != name + '|1':
            raise RuntimeError('Refuse cleanup of a container without the task ownership label')
        checked(['docker', 'rm', '-f', name], timeout=15)
        if checked(probe, timeout=15).strip():
            raise RuntimeError('Task-owned trial container cleanup could not be verified')


def engine_args(lock, inputs, outputs, name):
    limits = lock['limits']
    return ['docker', 'run', '--rm', '--name', name,
        '--label', 'life-atlas.audio-trial=1', '--platform', lock['image_platform'],
        '--network', 'none', '--cgroupns', 'private', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--log-driver', 'none',
        '--cpus', limits['cpu_quota'], '--memory', limits['memory'],
        '--memory-swap', limits['memory'], '--pids-limit', str(limits['pids']),
        '--user', '0:0', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
        '--mount', 'type=bind,source=' + str(STATE / 'models') + ',target=/models,readonly',
        '--mount', 'type=bind,source=' + str(inputs) + ',target=/input,readonly',
        '--mount', 'type=bind,source=' + str(outputs) + ',target=/output',
        '--entrypoint', '/bin/sh', lock['image'], '-c',
        deadline_script(limits['timeout_seconds']),
        'trial', 'whisper-cli', '-m', '/models/' + lock['model']['filename'],
        '-f', '/input/input.wav', '-l', 'en', '-t', str(limits['threads']),
        '-otxt', '-oj', '-of', '/output/transcript', '-np', '-ng']


def word_error_rate(reference, hypothesis):
    a = re.findall(r"[a-z]+", reference.lower())
    b = re.findall(r"[a-z]+", hypothesis.lower())
    row = list(range(len(b) + 1))
    for i, word in enumerate(a, 1):
        next_row = [i]
        for j, other in enumerate(b, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j-1] + (word != other)))
        row = next_row
    return row[-1] / max(1, len(a))


def run_trial(lock, source=None):
    if source is None:
        kind = 'public'
        source = STATE / 'public-fixtures' / lock['public_fixture']['filename']
        if sha256(source) != lock['public_fixture']['sha256']:
            raise RuntimeError('Public fixture integrity mismatch')
    else:
        kind = 'private-90s'
        source = Path(source)
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to((STATE / 'processed').resolve()):
            raise RuntimeError('Pilot source must be a regular archived audio file')
        if not re.fullmatch(r'log001-[0-9a-f]{12}\.opus', source.name):
            raise RuntimeError('Only the approved log001 pilot is permitted')
    model = STATE / 'models' / lock['model']['filename']
    if sha256(model) != lock['model']['sha256']:
        raise RuntimeError('Model integrity mismatch')
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    if available < 1800 * 1024 * 1024:
        raise RuntimeError('Insufficient memory headroom for this bounded trial')
    root = STATE / 'transcription-trials'
    private_dir(root)
    run = root / (dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + kind + '-' + uuid.uuid4().hex[:6])
    inputs = run / 'input'; outputs = run / 'output'
    private_dir(inputs); private_dir(outputs)
    source_hash = sha256(source)
    # Decode only the allowed excerpt; no sound playback or analysis.
    checked(['nice', '-n', '10', 'ffmpeg', '-nostdin', '-v', 'error', '-xerror',
        '-threads', '1', '-i', str(source), '-t', str(lock['limits']['pilot_seconds']),
        '-map', '0:a:0', '-vn', '-sn', '-dn', '-map_metadata', '-1',
        '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(inputs / 'input.wav')])
    clip = json.loads(checked(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(inputs / 'input.wav')]))
    name = 'life-atlas-audio-trial-' + uuid.uuid4().hex
    started = time.monotonic()
    try:
        with (run / 'engine-private.log').open('wb') as log:
            result = subprocess.run(engine_args(lock, inputs, outputs, name), stdout=log, stderr=log, timeout=lock['limits']['timeout_seconds'] + 30)
        if result.returncode:
            raise RuntimeError('Trial engine failed; inspect protected server state, not transcript logs')
    finally:
        # Only this randomly named task-owned container; never touch other services.
        cleanup_container(name)
    elapsed = time.monotonic() - started
    text_path = outputs / 'transcript.txt'
    json_path = outputs / 'transcript.json'
    text = text_path.read_text()
    if not text.strip() or not json_path.is_file():
        raise RuntimeError('Trial did not create nonempty transcript artifacts')
    transcript = json.loads(json_path.read_text())
    # Private content is inspected structurally only, never emitted to logs/chat.
    for path in outputs.iterdir():
        if path.is_file():
            path.chmod(0o600)
    peak_path = outputs / 'memory-peak.txt'
    peak = int(peak_path.read_text().strip()) if peak_path.exists() and peak_path.read_text().strip() else None
    receipt = {'kind': kind, 'source_sha256': source_hash,
        'clip_sha256': sha256(inputs / 'input.wav'), 'model_sha256': lock['model']['sha256'],
        'engine_image': lock['image'], 'language': 'en',
        'audio_seconds': float(clip['format']['duration']), 'elapsed_seconds': round(elapsed, 2),
        'peak_container_memory_bytes': peak, 'transcript_characters': len(text),
        'segments': len(transcript.get('transcription', [])),
        'transcript_sha256': sha256(text_path), 'timestamped_json_sha256': sha256(json_path),
        'output_directory': str(outputs), 'workflow_automation': False}
    if kind == 'public':
        receipt['public_sample_word_error_rate'] = word_error_rate(lock['public_fixture']['reference'], text)
        if receipt['public_sample_word_error_rate'] > 0.25:
            raise RuntimeError('Public sample transcription failed the basic quality gate')
    if sha256(source) != source_hash:
        raise RuntimeError('Archived source changed during trial')
    (run / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


def deadline_self_test(lock):
    # No private audio, transcription or model load: prove a container's own
    # deadline terminates its child independently of the SSH/Python client timer.
    with tempfile.TemporaryDirectory(prefix='audio-deadline-test-') as directory:
        root = Path(directory)
        inputs = root / 'input'; outputs = root / 'output'
        inputs.mkdir(); outputs.mkdir()
        name = 'life-atlas-audio-trial-' + uuid.uuid4().hex
        command = engine_args(lock, inputs, outputs, name)
        index = command.index('-c')
        command[index + 1] = deadline_script(1)
        command[index + 2:] = ['trial', 'sleep', '30']
        started = time.monotonic()
        try:
            result = subprocess.run(command, capture_output=True, timeout=30)
        finally:
            cleanup_container(name)
        if result.returncode != 124 or time.monotonic() - started > 15:
            raise RuntimeError('Independent container deadline test failed')
    print('PASS: independent container-side deadline and verified task-owned cleanup')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['install', 'public', 'pilot', 'self-test'])
    parser.add_argument('--source')
    args = parser.parse_args()
    lock = read_lock()
    if args.action == 'install':
        install(lock)
    elif args.action == 'self-test':
        deadline_self_test(lock)
    elif args.action == 'public':
        run_trial(lock)
    else:
        if not args.source:
            raise RuntimeError('An explicitly selected pilot source is required')
        run_trial(lock, args.source)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except Exception as exc:
        # Never surface download URLs with signed redirects, transcript text or engine logs.
        print('Audio transcription trial failed:', type(exc).__name__, '(private diagnostics suppressed)', file=__import__('sys').stderr)
        raise SystemExit(1)
