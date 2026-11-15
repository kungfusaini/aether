#!/usr/bin/env python3
"""Deploy approved local audio tools and inactive batch unit; no private content transfer."""
from pathlib import Path
import subprocess
import tarfile

SOURCE = Path(__file__).resolve().parent
FILES = {name: SOURCE/name for name in ['trial.py', 'trial.lock.json', 'test_trial.py',
    'review.py', 'test_review.py', 'batch.py', 'test_batch.py', 'daily.py', 'test_daily.py',
    'glossary.py', 'test_glossary.py', 'automation.py', 'test_automation.py', 'life-atlas-audio-intake.service',
    'life-atlas-audio-intake.timer', 'life-atlas-audio-batch@.service', 'README.md']}
FILES['transcript-review.SKILL.md'] = SOURCE.parents[1]/'.agents/skills/uncertain-transcript-review/SKILL.md'
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', 'aether']


def deploy():
    subprocess.run(['python3', '-B', '-m', 'unittest', 'discover', '-s', str(SOURCE), '-v'], check=True)
    subprocess.run(SSH + ['set -eu; umask 077; '
        'if test -e /opt/life-atlas-audio; then '
        'cp -a /opt/life-atlas-audio /opt/life-atlas-audio-backup-$(date -u +%Y%m%dT%H%M%SZ); fi; '
        'install -d -m 700 /opt/life-atlas-audio'], check=True)
    process = subprocess.Popen(SSH + ['umask 077; tar --no-same-owner -xf - -C /opt/life-atlas-audio'], stdin=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdin, mode='w|') as archive:
            for name, path in FILES.items():
                archive.add(path, arcname=name, recursive=False)
        process.stdin.close()
        if process.wait(timeout=60):
            raise RuntimeError('Trial source transfer failed')
    finally:
        if process.poll() is None:
            process.kill()
    subprocess.run(SSH + ['set -eu; chown -R root:root /opt/life-atlas-audio; '
        'chmod 600 /opt/life-atlas-audio/*; cd /opt/life-atlas-audio; '
        'python3 -B -m unittest -v; python3 -B trial.py install; '
        'install -d -m 700 /srv/audio-archive/transcription-batches; '
        'install -d -m 700 /srv/audio-archive/transcription-control; '
        'install -m 644 life-atlas-audio-batch@.service /etc/systemd/system/life-atlas-audio-batch@.service; '
        'systemd-analyze verify /etc/systemd/system/life-atlas-audio-batch@.service; '
        'systemctl daemon-reload'], check=True)


if __name__ == '__main__':
    deploy()
