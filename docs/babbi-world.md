# babbi.world operations

The private website repository is being handed to `AshBreaksTheCode/babbi.world`.
Aether continues to own hosting; the handoff does not change domain registration,
DNS, Mailcow, or the shared gateway's other sites.

## Deployment boundary

The website's `main` workflow builds and smoke-tests its ARM64 static Astro
image, publishes it to GHCR under the repository owner's lowercase namespace,
then dispatches `babbi-world-updated` to this repository. Its existing
`AETHER_DISPATCH_TOKEN` secret must authorize that dispatch; GitHub's per-run
`GITHUB_TOKEN` handles publishing to the website owner's package namespace.
Do not copy Aether SSH credentials into the website repository.

`deploy-babbi-world.yml` pulls and recreates only `babbi_world` with `--no-deps`.
It then tests and reloads the existing gateway so Nginx resolves the replacement
container's address. It does not recreate other services or prune rollback images.
The workflow reads the host's Compose files; it does not synchronize them.

On a repository transfer, update `docker-compose-prod.yml` to the new GHCR
namespace and verify the host's existing registry identity can read the new
private package. GitHub does not move granular GHCR packages automatically when
transferring a repository. Keep the previous image for rollback.

## Host state

- Compose root: `/var/www/containers`
- Container service: `babbi_world` on `aether-network`, internal port 80
- Restart policy: `unless-stopped`
- Site files: only Astro's `dist/` in the image, no host content volume
- Certificate: `/etc/letsencrypt/live/babbi.world/`
- Renewal hook: source `scripts/ssl-renewal-hook.sh`; install its executable copy
  at `/etc/letsencrypt/renewal-hooks/deploy/ssl-renewal-hook.sh`

Apply approved host configuration separately from the full-platform workflow.
Do not invoke the full Aether deployment just to publish this website.

## Checks

- Verify the source Deploy action **and** the Aether redeploy action succeeded.
- Inspect the running image's `org.opencontainers.image.revision` label and
  restart policy; compare with the published `main` commit.
- Check both HTTPS hostnames and representative routes, plus a built asset.
- `/.git/HEAD`, `/.github/workflows/deploy.yml`, `/Dockerfile`, and `/package.json`
  must return 404, through both the origin and Cloudflare.
- Check the served certificate; its renewal must trigger the installed hook.

Sanity content changes need a rebuild; the source repository supports a manual
Deploy run on `main`. The optional `postoffice/` newsletter is not provisioned by
this handoff. Preserve the domain's existing mail DNS records.
