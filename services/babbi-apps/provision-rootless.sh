#!/bin/bash
# One-time host provisioning from a reviewed copy of this directory.
# Refuses existing workspace data rather than formatting/replacing it.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as Aether administrator.' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
[[ ! -e /home/babbi-apps && ! -e /var/lib/babbi-apps ]] || {
    echo 'Existing workspace: inspect it and apply reviewed updates separately.' >&2; exit 1;
}
! getent passwd 2000 >/dev/null || { echo 'UID 2000 already exists.' >&2; exit 1; }
[[ $(stat -fc %T /sys/fs/cgroup) == cgroup2fs ]]
command -v dockerd-rootless-setuptool.sh >/dev/null
# Install only the missing helpers; do not upgrade/restart the production engine.
DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get --no-install-recommends --no-upgrade install -y uidmap slirp4netns
install -d -m 700 /var/lib/babbi-apps
fallocate -l 8G /var/lib/babbi-apps/workspace.ext4
# Preserve fallocate's host-disk reservation instead of discarding it at mkfs.
mkfs.ext4 -q -E nodiscard -m 0 -L babbi-apps /var/lib/babbi-apps/workspace.ext4
install -d -m 755 /home/babbi-apps
printf '%s\n' '/var/lib/babbi-apps/workspace.ext4 /home/babbi-apps ext4 loop,nodev,nosuid,nodiscard,nofail,X-fstrim.notrim,x-systemd.device-timeout=10s 0 2' >> /etc/fstab
mount /home/babbi-apps
useradd --uid 2000 --user-group --home-dir /home/babbi-apps --shell /usr/sbin/nologin --no-create-home --no-log-init babbi-apps
chown babbi-apps:babbi-apps /home/babbi-apps
chmod 700 /home/babbi-apps
# useradd allocates subordinate IDs on Debian. Validate before starting anything.
awk -F: '$1=="babbi-apps" && $3>=65536 {ok=1} END {exit !ok}' /etc/subuid
awk -F: '$1=="babbi-apps" && $3>=65536 {ok=1} END {exit !ok}' /etc/subgid
install -d /etc/babbi-apps /etc/systemd/system/user@2000.service.d /etc/systemd/system/user-2000.slice.d
install -m 644 "$source_dir/user-manager.conf" /etc/systemd/system/user@2000.service.d/babbi-apps.conf
install -m 644 "$source_dir/workspace.slice.conf" /etc/systemd/system/user-2000.slice.d/babbi-apps.conf
install -m 644 "$source_dir/egress.nft" /etc/babbi-apps/egress.nft
install -m 644 "$source_dir/egress.service" /etc/systemd/system/babbi-apps-egress.service
# Preserve isolation on the host's authoritative nftables reload path too.
include='include "/etc/babbi-apps/egress.nft"'
grep -Fxq "$include" /etc/nftables.conf || printf '\n# Babbi rootless workspace isolation\n%s\n' "$include" >> /etc/nftables.conf
nft -c -f /etc/nftables.conf
install -d -o babbi-apps -g babbi-apps -m 700 /home/babbi-apps/.config /home/babbi-apps/.config/docker /home/babbi-apps/tmp
install -o babbi-apps -g babbi-apps -m 600 "$source_dir/daemon.json" /home/babbi-apps/.config/docker/daemon.json
systemctl daemon-reload
systemctl enable --now babbi-apps-egress.service
loginctl enable-linger babbi-apps
systemctl start user@2000.service
# --force explicitly keeps the main/rootful Docker service running.
runuser -u babbi-apps -- env HOME=/home/babbi-apps XDG_RUNTIME_DIR=/run/user/2000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/2000/bus dockerd-rootless-setuptool.sh install --force
printf '%s\n' 'Rootless foundation ready. Deploy the platform and initialize its private credentials separately.'
