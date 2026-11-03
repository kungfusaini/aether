#!/usr/bin/env python3
"""Deploy only this offline service over existing trusted admin SSH.
Requires explicit approval. Never transfers location data or credentials.
"""
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent
FILES = ['history.py', 'test_history.py', 'README.md', 'activate.sh',
         'life-atlas-history.service', 'life-atlas-history.timer']
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', 'aether']


def remote(command):
    subprocess.run(SSH + [command], check=True)


if __name__ == '__main__':
    subprocess.run(['python3', '-B', '-m', 'unittest', 'discover', '-s', str(ROOT), '-v'], check=True)
    remote('''set -eu
umask 077
if test -e /opt/life-atlas-history; then
    cp -a /opt/life-atlas-history /opt/life-atlas-history-backup-$(date -u +%Y%m%dT%H%M%SZ)
fi
systemctl stop life-atlas-history.timer 2>/dev/null || true
install -d -m 700 /opt/life-atlas-history /srv/life-atlas/history /srv/life-atlas/history/raw-snapshots
''')
    process = subprocess.Popen(SSH + ['umask 077; tar --no-same-owner -xf - -C /opt/life-atlas-history'], stdin=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdin, mode='w|') as archive:
            for name in FILES:
                archive.add(ROOT / name, arcname=name, recursive=False)
        process.stdin.close()
        if process.wait(timeout=60):
            raise RuntimeError('source transfer failed')
    finally:
        if process.poll() is None:
            process.kill()
    remote('''set -eu
chown -R root:root /opt/life-atlas-history
chmod 600 /opt/life-atlas-history/*
cd /opt/life-atlas-history
python3 -B -m unittest -v
install -m 644 life-atlas-history.service /etc/systemd/system/life-atlas-history.service
install -m 644 life-atlas-history.timer /etc/systemd/system/life-atlas-history.timer
systemd-analyze verify /etc/systemd/system/life-atlas-history.service /etc/systemd/system/life-atlas-history.timer
sh /opt/life-atlas-history/activate.sh
''')
