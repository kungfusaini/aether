# Babbi apps: bounded self-service workspace on Aether

This is separate from the existing `babbi_world` website service and the main
rootful Docker engine. It runs a dedicated rootless engine as `babbi-apps`
(UID/GID 2000), with no sudo, Docker group membership, or interactive SSH login.
Portainer CE manages only that rootless socket. Traefik routes her app containers.
A dedicated Cloudflare Tunnel is the intended public ingress; no new public host
ports or changes to Aether's shared Nginx gateway are required.

## Capacity and isolation

- `user-2000.slice`: CPUQuota 100% (one core's worth), MemoryHigh 896 MiB,
  MemoryMax 1 GiB, no swap allowance, 512 tasks. Includes engine, UI, edge and apps.
- A preallocated **8 GiB ext4 image** at `/var/lib/babbi-apps/workspace.ext4`, mounted
  at `/home/babbi-apps` with nodev/nosuid and a nonfatal (`nofail`) host-boot mount.
  Her manager still requires the mount, so only her workspace fails if unavailable.
  The backing file is root-owned in a root-only directory. `mkfs -E nodiscard`
  preserves its physical allocation; `nodiscard,X-fstrim.notrim` protects that
  reservation from the host's scheduled fstab-based trim. Do not manually trim the
  loop filesystem with `fstrim -a`. Verification checks allocated blocks, not just
  apparent file size. Images, volumes, Portainer state and app files live here.
- Only this user's systemd manager gets the hardening drop-in. Host files are
  read-only outside home/runtime; protected production paths and rootful socket
  are masked. `/tmp` and `/var/tmp` are private 32 MiB tmpfs mounts, charged to RAM.
  No other user manager or production service is changed.
- The manager's private device namespace exposes only the TUN device needed for
  rootless networking. Unprivileged user namespaces and subordinate IDs are used.
- A dedicated nftables table blocks this account's new host-side connections to
  private/loopback/link-local networks and the Aether host addresses, while
  allowing replies and public egress. It never flushes Docker/Mailcow rules itself.
  `/etc/nftables.conf` includes `/etc/babbi-apps/egress.nft`, preserving these rules
  on the host's authoritative reload path. The oneshot is ordered after and
  participates in nftables service restarts. Never reload the full firewall merely
  to apply this workspace table; use its dedicated service. Unreviewed raw firewall
  flushes can still remove protections and disrupt Docker/Mailcow.
- Container logs default to 2 × 5 MiB files. User-manager logs are rate-limited;
  host journal storage remains managed by the host, outside the workspace image.

This is **not a VM or hostile multi-tenant security boundary**. It shares the
kernel, disk I/O and public IP. Rootless app admins control their whole workspace,
including its router and dashboard. Do not grant them the main Docker socket,
sudo, arbitrary root SSH, or rootful Portainer access. Keep the host patched.

## Files and ownership

| Source | Installed target |
| --- | --- |
| `user-manager.conf` | `/etc/systemd/system/user@2000.service.d/babbi-apps.conf` |
| `workspace.slice.conf` | `/etc/systemd/system/user-2000.slice.d/babbi-apps.conf` |
| `egress.nft` | `/etc/babbi-apps/egress.nft` |
| `egress.service` | `/etc/systemd/system/babbi-apps-egress.service` |
| `daemon.json` | `/home/babbi-apps/.config/docker/daemon.json` |
| `compose.yml`, `demo/` | `/home/babbi-apps/platform/` |

The system drop-ins, nft rules, backing image and provisioning helpers are
root-owned. User runtime/data files belong to `babbi-apps`. Nothing runs from a
Git checkout. Runtime secrets and state must never be committed.

## Provision a fresh workspace

Review host capacity first. Copy this directory to an administrator-controlled
host staging path and run `provision-rootless.sh` as root. It is intentionally
one-time: it refuses pre-existing workspace data or UID 2000 and formats **only a
newly allocated file**, never a host block device. It installs just uidmap and
slirp4netns helpers, with no package upgrades or production daemon restart.

The rootful Docker service stays running. `dockerd-rootless-setuptool.sh install
--force` sets up the separate user service. Linger and the mount dependency make
it persistent across logout/reboot. Do not rerun the provisioning script to update
an existing workspace: apply reviewed source changes to their installed targets.
Restarting user@2000 affects only her workspace, but interrupts all of her apps.

## Initialize the platform securely

1. Copy `compose.yml` and `demo/` into `/home/babbi-apps/platform`, owned by UID 2000.
2. Create `.secrets/` mode 700 and a random `portainer-admin-password` file mode 600
   owned by UID 2000. Do not display it in chat/logs. Create an empty mode-600
   `tunnel-token` placeholder while public ingress is not yet configured.
3. From that directory, target only `DOCKER_HOST=unix:///run/user/2000/docker.sock`
   as `babbi-apps`, and run `docker compose up -d edge portainer demo`.
4. Run `bootstrap-portainer.py` as the Aether administrator. It verifies that the
   sole environment is rootless and creates a private deployment-token file under
   `.secrets/`. It never prints credentials. This is an initial bootstrap helper,
   not a password-reset mechanism after human handoff.
5. Verify localhost routes on 18080 with `Host: demo.babbi.world` and
   `Host: apps.babbi.world`. Direct Portainer bootstrap/API is bound to 127.0.0.1:19000.
   Unauthenticated `/api/endpoints` must be 401. Neither port should bind publicly.
6. Complete public ingress and a secure human password handoff before calling
   this workspace usable by its owner. Change the generated admin password and
   remove obsolete bootstrap plaintext/key files after replacing any credentials
   that still depend on them. Do not paste them into a transcript.

Portainer CE's deployment token can manage **her whole rootless environment**.
Create separate named tokens for trusted repos when needed. The example in
`examples/babbi-app/` builds in GitHub Actions and deploys through the API; it does
not rely on paid Portainer webhooks or a new privileged SSH endpoint.

## Public ingress (separate, scoped Cloudflare setup)

Create a dedicated remotely managed tunnel named `babbi-apps`. Its ingress should
route `*.babbi.world` to `http://edge:8080`, with a final HTTP-404 catchall. Store
only that tunnel's connector token in `.secrets/tunnel-token`; never give the
workspace a broad Cloudflare account token. Then start the `tunnel` Compose profile.

After verifying existing DNS records, add a proxied wildcard CNAME `*.babbi.world`
to `<tunnel-uuid>.cfargotunnel.com`. Preserve **all existing explicit DNS records**,
including apex, www and email records, which take precedence over the wildcard.
Do not move nameservers, registration or the existing website. Unknown app hosts
should return 404; `apps.babbi.world` serves the authenticated dashboard and
`demo.babbi.world` the harmless static demo. Verify edge HTTPS, host routing,
unauthenticated management denial, and the existing website/email endpoints.

No Cloudflare configuration or public URL is implied merely by installing the
local platform. Record actual tunnel/DNS status in the project worklog.

## Operator maintenance through existing Aether CI

`.github/workflows/babbi-apps-maintenance.yml` is manual-only and uses Aether's
existing operator SSH secrets, not credentials from her repositories. It pins the
server's previously trusted Ed25519 public key via `aether_known_hosts` and keeps
strict host verification. It does not change SSH/firewall policy.

- `inspect`: read workspace service/resource state and check the local demo.
- `start-tunnel`: stage the reviewed platform configuration (refusing unexpected
  edits), install sealed `BABBI_APPS_TUNNEL_TOKEN` as a private UID-2000 file,
  recreate **only cloudflared**, and require its localhost `/ready` check. Secret
  rotation requires recreation because bind-mounted file inodes otherwise persist.
  The connector runs as root **inside the rootless namespace**, with capabilities
  dropped and read-only root filesystem, so it can read the UID-2000 mode-600 file.
- `encrypted-handoff`: encrypt the machine-generated initial Portainer password
  directly from SSH to a caller-supplied **public age recipient**. The one-day
  artifact contains ciphertext only. Keep the private recipient key local, decrypt
  only in a private terminal for the human handoff, and rotate the initial password.
  Never pass a real password as a workflow input or print it in logs/chat.

The connector's metrics/readiness port is bound only to 127.0.0.1:12000. If the
laptop's SSH path is unavailable, the approved CI route can still perform these
scoped operations without widening her server privileges or unbanning an IP.

## Verification and recovery

- `bash verify-host.sh` on Aether: bounded live checks of rootless mode, caps,
  protected paths, actual rootful socket connection denial, private-network denial,
  authenticated dashboard and local demo routing. The private-network test checks
  TCP connection denial against a confirmed host listener, not HTTP status, and
  independently checks public egress. No load test or image pruning.
- `python3 test-deploy.py` from this checkout: offline deployment-client checks.
- Inspect `/sys/fs/cgroup/user.slice/user-2000.slice/memory.events` for pressure/OOM.
  Cache is included in MemoryCurrent; high events alone do not mean an OOM.
- Check the new filesystem's free space separately from the host root filesystem.
- Restart the user Docker service or reapply managed platform Compose only against
  her user socket. Never run `docker compose down` on the main Aether stack.
- To disable her public ingress, stop only her tunnel connector; preserve volumes,
  image, account and source. Deleting the workspace/backing file needs new approval.

Backups for actual app data are a separate required step before storing important
information. The demo has no private data or database; this setup does not pretend
that an untested backup exists.

## Primary references

- [Docker rootless requirements](https://docs.docker.com/engine/security/rootless/)
- [Rootless cgroup/resource limits](https://docs.docker.com/engine/security/rootless/tips/)
- [Portainer rootless setup](https://portainer.io/blog/portainer-and-rootless-docker)
- [Portainer CE/ARM64 compatibility](https://docs.portainer.io/start/requirements-and-prerequisites)
- [Cloudflare Tunnel DNS](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/dns/)
