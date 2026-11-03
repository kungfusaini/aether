#!/bin/sh
# Called after source tests and systemd unit validation, not before.
set -eu
systemctl daemon-reload
# Enable retries before initial ingestion: a partial upload must not leave the
# timer stopped when this initial service invocation returns nonzero.
systemctl enable --now life-atlas-history.timer
if ! systemctl start life-atlas-history.service; then
    journalctl -u life-atlas-history.service -n 15 --no-pager
    exit 1
fi
systemctl show life-atlas-history.service -p Result -p ExecMainStatus
systemctl show life-atlas-history.timer -p ActiveState -p NextElapseUSecMonotonic
cat /srv/life-atlas/history/summary.json
