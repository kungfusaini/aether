# babbi.world operations

`AshBreaksTheCode/babbi.world` owns the private website source and deploy workflow.
Aether continues to own hosting; this does not change domain registration, DNS,
Mailcow, Sanity, or the shared gateway's other sites.

## Deployment boundary

The source repository's `main` workflow builds and smoke-tests its ARM64 static
Astro image, then publishes `ghcr.io/ashbreaksthecode/babbi-world:<commit-sha>` and
`:latest`. It connects to Aether using a dedicated SSH key. There is no longer an
Aether repository-dispatch workflow or `AETHER_DISPATCH_TOKEN` dependency.

The key's entry in `/root/.ssh/authorized_keys` must have this prefix:

```
restrict,command="/usr/bin/timeout --kill-after=90 600 /usr/local/sbin/deploy-babbi-world"
```

OpenSSH's `restrict` disables PTY, port/agent/X11 forwarding, and user RC files.
The root-owned command accepts only `deploy <40-hex-commit> <github-actor>`.
Other commands (including shell and SFTP requests) are rejected. It accepts no
caller-provided image name, Compose path, service name, or script.

Source: `scripts/deploy-babbi-world.sh`. Install executable and root-owned at
`/usr/local/sbin/deploy-babbi-world`. Test without real Docker/SSH/credentials via
`python3 scripts/test-deploy-babbi-world.py`.

## Credentials

The website repository has these Actions secrets:

- `BABBI_DEPLOY_SSH_KEY`: dedicated Ed25519 private key, never Sumeet's admin key
- `BABBI_DEPLOY_HOST`: Aether's SSH host address
- `BABBI_DEPLOY_KNOWN_HOSTS`: verified server host public key, checked strictly

The workflow sends its short-lived `GITHUB_TOKEN` over encrypted SSH stdin for
the private image pull. The command uses an isolated temporary Docker login under
`/run`, removes it on exit, and does not modify `/root/.docker/config.json` or
other services' registry credentials. No long-lived GitHub PAT is required.
The deployment capability still permits running this site's container image;
protect the repository and its Actions secrets accordingly.

To rotate: generate a fresh dedicated key, append its **restricted** public-key
entry, update the source repository secret, verify a deployment, then revoke only
the old restricted entry. Never alter the administrator's authorized keys. To
revoke website deployment entirely, remove its restricted authorized-key entry.
Verify host keys through the existing trusted admin SSH connection when rotating;
never trust an unverified `ssh-keyscan` result or disable host checking.

## Rollout and rollback

The deploy command serializes requests with a host lock, pulls only the fixed
website package, verifies repository/revision labels, and pins the image digest.
It recreates only `babbi_world` with `--no-deps --pull never`, waits for its HTTP
listener, reloads the existing gateway to refresh upstream DNS, and checks origin
HTTPS. Other containers are not recreated and images are not pruned.

Before replacement the old image is retained locally as
`ghcr.io/ashbreaksthecode/babbi-world:rollback`. Recreation or health-check failure
attempts rollback to the exact old image ID and reports failure to the Action.
Unexpected errors and timeout signals after replacement also trigger bounded
rollback; the SSH command reserves 90 seconds after TERM before forced cleanup.
This is not a blue/green rollout; a failed image may cause a short interruption.

For an operator's cached-image rollback in `/var/www/containers`, use the usual
four Compose files and set `BABBI_WORLD_IMAGE` to the retained image ID/tag for
`up -d --no-deps --pull never --scale babbi_world=1 babbi_world`, then test/reload
the gateway and verify HTTPS. Do not invoke the full Aether deployment.

## Host state and checks

- Compose root: `/var/www/containers`
- Compose image: `${BABBI_WORLD_IMAGE:-ghcr.io/ashbreaksthecode/babbi-world:latest}`
- Service: `babbi_world` on `aether-network`, internal port 80, no host content mount
- Restart policy: `unless-stopped`
- Runtime files: only Astro's `dist/`; hidden/build metadata paths return 404
- Certificate: `/etc/letsencrypt/live/babbi.world/`
- Renewal hook: `scripts/ssl-renewal-hook.sh`, installed executable under
  `/etc/letsencrypt/renewal-hooks/deploy/ssl-renewal-hook.sh`

Verify the source **Deploy** Action succeeded, the running revision label matches
its `main` commit, both HTTPS names and representative routes work, and Git/build
metadata paths return 404 through both origin and Cloudflare. Compare other
containers' IDs before/after when changing the deploy implementation.

Sanity content changes still need a rebuild: run source **Deploy** manually on
`main`. The optional newsletter service is not provisioned by this workflow.
