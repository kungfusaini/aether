# A new app in Babbi's workspace

Copy **the contents of this directory**, including `.github/`, into your own new
GitHub repository. Do not replace the existing `babbi.world` repository or its
working website deployment.

## One-time setup for each repository

1. Replace the sample `Dockerfile` and `index.html` with your app. The image must
   support ARM64. Build in GitHub Actions, not on the small Aether workspace.
2. Set repository **Actions variables**:
   - `BABBI_APP_NAME`: e.g. `financeapp` (lowercase letters, digits, hyphens).
   - `BABBI_APP_PORT`: your app's internal HTTP port, e.g. `3000`; sample uses `80`.
3. Create a deployment API token in your Portainer profile at
   `https://apps.babbi.world`. Save it as the repository **Actions secret**
   `BABBI_APPS_TOKEN`. This gives that repository deployment access to **all of
   your app workspace**, not to the main Aether engine. Only give it to trusted
   repositories; revoke unused tokens. No server SSH or personal GitHub token.
4. Keep `compose.yml`'s routing labels and `babbi-edge` network. Add app-specific
   variables in Portainer and refer to them in Compose. The deployment client
   preserves existing stack environment values.
5. Push to `main`. Watch **Deploy app** for build, private image publication,
   matching running-image verification, and public HTTP health results.

When the wildcard app routing is configured, `financeapp` becomes
`https://financeapp.babbi.world` without another server-side Nginx change. Existing
explicit DNS records take precedence. Do not use reserved names `apps`, `demo`,
`www`, `mail`, or `babbi-platform`. DNS remains under your control in Cloudflare.

## Managing it

In Portainer, select **Babbi rootless apps**, then **Stacks**. You can edit
configuration, view containers/logs, restart, or stop your apps. Other Aether
containers are not part of this environment. The platform's containers are also
in your workspace: deleting its edge router or Portainer will interrupt your
own access; ask Sumeet to restore the platform from its managed configuration.

The complete workspace has **one CPU's worth, 1 GiB RAM and an 8 GiB filesystem**,
including images and the dashboard, not that much per app. The template limits
one app to 256 MiB; account for any databases too. It shares the host kernel and
internet connection, so it is not VM-level isolation or unlimited capacity.

- Use named volumes for durable database/app data; do not bind host directories.
- Keep databases on an app-private network. Only the web service belongs on
  `babbi-edge`; do not publish database ports.
- Add authentication before exposing private or financial information.
- Arrange tested backups before storing real data. This template does **not**
  create backups or do database migrations.
- Do not build large images on the server or prune volumes to make room.
- No automatic rollback is performed by this simple template. If a deployment
  fails, inspect its Action and stack logs; redeploy a previous known-good image
  without deleting data volumes. HTTP 200 alone is not proof of app correctness.

The API client pulls with the Actions run's temporary `GITHUB_TOKEN`, pins the
image digest, and verifies its image ID is running. The management API token is
sent only to Portainer over HTTPS, never to your app's health URL or the registry.
Portainer CE's stack webhooks require a paid edition, so this template uses the
existing authenticated API instead of adding another deployment service.
