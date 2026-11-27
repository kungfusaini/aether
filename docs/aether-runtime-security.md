# Aether container runtime security maintenance

On 2026-11-27 the existing Debian 13 ARM64 installation was upgraded through
Docker's official signed APT repository:

| Component | Before | Verified after |
| --- | --- | --- |
| Docker Engine, CLI and rootless extras | 28.5.0 | 29.10.1 |
| containerd | 1.7.28 | 2.4.2 |
| runc (provided by containerd.io) | 1.3.0 | 1.5.3 |

This addresses the outdated runtime identified during the Babbi security audit;
see Docker's [28.5.2 security notice](https://docs.docker.com/engine/release-notes/28/#2852)
and [current release notes](https://docs.docker.com/engine/release-notes/29/).
It does not establish that every application image or host package is patched.

## Scope and recovery precautions

Only four runtime packages changed. Two network compatibility settings were
also applied as described below. No application images, Compose stacks,
volumes or Docker storage drivers were replaced or pruned. Both existing engines
use the original container IDs. Restore previously running `restart=no`
containers explicitly after engine maintenance; otherwise they remain stopped.

Rootful live-restore was disabled. Service interruption was approved; do not
promise zero downtime for future runtime upgrades. Recheck Docker API client
compatibility, especially Mailcow, its scheduler, Vulkan and Portainer, before a
major engine update. Docker 29.10's minimum supported API is 1.40.

Root-only recovery material is in `/var/backups/aether-runtime-20261127`:
configuration copies, package versions, baseline running-container IDs, install
log, downloaded upgrade packages and fallback Docker 28.5.2/containerd 1.7.29
packages. This is **not** a complete application-data backup or a tested full
engine-state rollback. Do not delete runtime metadata or volumes to downgrade.
Evaluate state compatibility before using the fallback packages.

The existing restricted website deployment lock was held during installation.
No GitHub workflows were running before or after; the available GitHub credential
could not disable workflows (403), so full deployment exclusion was not available.
Coordinate a maintenance window rather than assuming that lock covers all CI.

## Rootless network compatibility

Docker 29 enables RootlessKit's detached-netns mode by default. In this workspace,
embedded DNS timed out after upgrading while direct IPv4 resolver queries worked;
cloudflared could not establish its tunnel. The supported upstream compatibility
switch `DOCKERD_ROOTLESS_ROOTLESSKIT_DETACH_NETNS=false` restored the previous
isolated daemon network and recovered embedded DNS and public HTTPS egress.

Source: `services/babbi-apps/rootless-docker.conf`, deployed as UID 2000 to
`/home/babbi-apps/.config/systemd/user/docker.service.d/network.conf` through a
service inside the hardened user manager. Root did not write through
workspace-owned pathnames. The provisioner installs this override for fresh
workspaces as well.

After deployment, the temporary diagnostic manager environment was removed and
the engine restarted again. The persistent override alone passed DNS, public
egress and tunnel readiness checks. No firewall exceptions or DNS/TLS policy
bypasses were added. See the [upstream rootless launcher](https://github.com/moby/moby/blob/master/contrib/dockerd-rootless.sh)
for the compatibility option.

## Rootful DNS compatibility

Post-upgrade checks caught the two Telegram bots restarting with DNS errors.
Docker's embedded resolver timed out, while direct queries to the existing
provider IPv4 resolver `185.12.64.1` succeeded. Both configured provider IPv6
resolvers also timed out when queried directly from the host; the audit did not
establish when that underlying IPv6 problem began.

`services/docker-engine/daemon.json` is the reviewed source for
`/etc/docker/daemon.json`. It preserves the existing `ipv6: true` setting and
sets Docker's default resolver to the same provider IPv4 address already present
in the host resolver configuration. This is not a different DNS provider, a host
or laptop resolver change, or a firewall exception. Containers with explicitly
configured DNS retain their own overrides. The single default IPv4 resolver is
not redundant; repairing provider IPv6 reachability or adding another verified
provider resolver is separate work.

The configuration passed `dockerd --validate` before installation. After restarting
the main engine and restoring baseline IDs, embedded resolution of
`api.telegram.org` succeeded. Retain the original configuration in the recovery
directory, but do not restore it blindly while those IPv6 resolvers time out.

## Verification evidence

- Both engines report 29.10.1; installed runc reports 1.5.3.
- All 33 rootful and 5 rootless previously running container IDs were restored.
- Main gateway configuration, Mailcow Docker SDK ping and Vulkan Dockerode
  container listing passed after the upgrade.
- Main website, Babbi apex/www, mail UI and analytics HTTPS checks passed.
- Mail service TCP listeners were available after restart.
- Dashboard and demo returned public HTTPS 200; anonymous management returned 401.
- Rootless systemd cgroup limits and its dedicated nftables table remained active.

The separate plan to add a free Cloudflare Access gate to `apps.babbi.world`
requires an explicit human identity allowlist and CI service credentials. The
existing Portainer password remains required; do not add a public API bypass or
protect all wildcard app hosts by accident. Access was not enabled as part of the
runtime update.
