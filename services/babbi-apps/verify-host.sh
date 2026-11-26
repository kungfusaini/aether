#!/bin/bash
# Bounded live checks. Uses only her rootless daemon; no stress test or cleanup.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
d() {
    runuser -u babbi-apps -- env HOME=/home/babbi-apps XDG_RUNTIME_DIR=/run/user/2000 DOCKER_HOST=unix:///run/user/2000/docker.sock docker "$@"
}
[[ $(id -u babbi-apps) == 2000 ]]
[[ $(getent passwd babbi-apps | cut -d: -f7) == /usr/sbin/nologin ]]
[[ $(id -nG babbi-apps) == babbi-apps ]]
[[ $(stat -c %s /var/lib/babbi-apps/workspace.ext4) == 8589934592 ]]
allocated=$(( $(stat -c %b /var/lib/babbi-apps/workspace.ext4) * $(stat -c %B /var/lib/babbi-apps/workspace.ext4) ))
[[ $allocated -ge 8589934592 ]]
awk '$2=="/home/babbi-apps" && $4 ~ /(^|,)X-fstrim.notrim(,|$)/ {ok=1} END {exit !ok}' /etc/fstab
[[ $(systemctl show user-2000.slice -p MemoryMax --value) == 1073741824 ]]
[[ $(systemctl show user-2000.slice -p CPUQuotaPerSecUSec --value) == 1s ]]
d info --format '{{json .SecurityOptions}}' | grep -q 'name=rootless'
[[ $(d info --format '{{.CgroupDriver}}') == systemd ]]
for path in /var/www/containers /opt/mailcow-dockerized /run/secrets; do
    if d run --rm -v "$path:/probe:ro" public.ecr.aws/docker/library/alpine:3.22 ls /probe >/dev/null 2>&1; then
        echo "Unexpected protected-path access: $path" >&2; exit 1
    fi
done
# Listing a masked socket is not an access test: actually try connecting to it.
if d run --rm -v /run/docker.sock:/probe:ro public.ecr.aws/docker/library/python:3.13-alpine python -c 'import socket; s=socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect("/probe")' >/dev/null 2>&1; then
    echo 'Unexpected rootful Docker API access' >&2; exit 1
fi
d run --rm --privileged -v /:/host public.ecr.aws/docker/library/alpine:3.22 sh -ec 'if touch /host/var/lib/babbi-isolation-probe 2>/dev/null; then exit 1; fi'
# Confirm a listener first; an HTTP 404 must not be mistaken for isolation.
python3 -c 'import socket; socket.create_connection(("172.19.0.1",80),timeout=3).close()'
set +e
d run --rm public.ecr.aws/docker/library/python:3.13-alpine python -c '
import socket,sys
try:
    socket.create_connection(("172.19.0.1",80),timeout=3).close()
except OSError:
    sys.exit(42)
sys.exit(0)'
blocked=$?
set -e
[[ $blocked == 42 ]] || { echo "Private TCP denial failed (status $blocked)" >&2; exit 1; }
d run --rm public.ecr.aws/docker/library/alpine:3.22 wget -q -T 8 -O /dev/null https://example.com
nft list chain inet babbi_apps output >/dev/null
[[ $(curl --max-time 5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:19000/api/endpoints) == 401 ]]
curl --max-time 5 -fsS -H 'Host: demo.babbi.world' http://127.0.0.1:18080/ | grep -q 'Babbi Apps is ready'
echo 'Live rootless, resource-boundary, protected-path, private-network and authenticated-dashboard checks passed.'
