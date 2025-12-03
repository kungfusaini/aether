# Aether - Unified Docker Setup

Complete Docker Compose orchestration for web applications with integrated services.

## Architecture

- **sumeetsaini_com**: [Frontend](https://github.com/kungfusaini/sumeetsaini_com) web application
- **vulkan**: [Backend](https://github.com/kungfusaini/vulkan) API service
- **arcanecodex**: [Hugo](https://github.com/kungfusaini/arcane-codex) static site
- **gateway**: Nginx reverse proxy with SSL termination
- **plausible**: Self-hosted analytics (optional in dev, enabled in production)
- **mailcow**: Email service (production only)

## Development

```bash
# Start development environment (without analytics)
docker compose -f docker-compose.yml -f docker-compose-dev.yml up -d

# Start with Plausible analytics (optional)
docker compose -f docker-compose.yml -f docker-compose-dev.yml -f docker-compose-plausible.yml up -d

# View logs
docker compose logs -f

# Stop services
docker compose down
```

**Access:**
- Frontend: http://localhost
- API: http://vulkan.localhost
- Arcane Codex: http://arcanecodex.localhost
- Analytics (if enabled): http://stats.localhost

**Setup for development with Plausible:**
1. Add to `/etc/hosts`: `127.0.0.1 stats.localhost`
2. Visit http://stats.localhost/register to create an account
3. Add sites: `sumeetsaini.com` and `arcanecodex.dev`
4. Note: localhost traffic is automatically filtered by Plausible

**Email:** Enabled by default with auto-generated Ethereal credentials for testing.

## Production Deployment

```bash
# Deploy all services (including Plausible analytics)
docker compose -f docker-compose.yml -f docker-compose-prod.yml -f docker-compose-mailcow.yml -f docker-compose-plausible.yml up -d
```

**Production Analytics Setup:**
1. Obtain SSL certificate: `sudo certbot certonly --standalone -d stats.sumeetsaini.com`
2. Visit https://stats.sumeetsaini.com/register to create admin account
3. Add sites: `sumeetsaini.com` and `arcanecodex.dev`
4. Analytics are automatically tracked on production domains

## SSL Management

Wildcard certificates managed via Let's Encrypt with automatic renewal.

## Deployment Pipeline

Automated via GitHub Actions on push to main:
1. Builds Docker images (linux/arm64)
2. Pushes to GitHub Container Registry
3. Syncs configs to production server
4. Deploys services with secrets
5. Runs health checks
6. Cleans up old images

**Security**: All secrets managed via GitHub Secrets

## Features

- Modern web application frontend
- RESTful API backend
- Static site generation with Hugo
- Self-hosted privacy-friendly analytics
- Rate-limited contact system
- Security-hardened services
- Containerized deployment
- Unified Docker orchestration
- SSL certificate management
- Integrated email service
