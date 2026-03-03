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

### How SSL Renewal Works

1. **Webroot Method**: Certbot places verification files in `/var/www/letsencrypt/.well-known/acme-challenge/` on the host
2. **Nginx serves challenges**: HTTP server blocks include a location to serve these files before redirecting to HTTPS
3. **Automatic renewal**: Systemd timer runs `certbot renew` twice daily; deploy hook restarts nginx after renewal

### Adding a New Website

Follow these steps to add a new domain to the gateway:

#### 1. Create Nginx Configuration

Create a new config file in `services/gateway/conf.d/prod/` named `<domain>.conf`:

```nginx
# <domain> - Production (HTTPS)

# HTTP to HTTPS redirect with ACME challenge support
server {
    listen 80;
    listen [::]:80;  # IPv6 support (required if domain has AAAA record)
    server_name <domain> www.<domain>;

    # ACME challenge for certbot renewal (must come before redirect)
    location /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS server
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;  # IPv6 HTTPS
    server_name <domain> www.<domain>;
    
    # security configuration
    include /etc/nginx/conf.d/common/security.conf;
    
    # SSL certificates (will exist after certbot)
    ssl_certificate /etc/letsencrypt/live/<domain>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/<domain>/privkey.pem;

    location / {
        proxy_pass http://<your_backend_service_name>_prod;
        include /etc/nginx/conf.d/common/proxy-headers.conf;
    }

    # Add additional locations as needed (e.g., /stats/, /api/, /vulkan/)
}
```

**Important**:
- Replace `<domain>` with your actual domain
- Replace `<your_backend_service_name>` with the Docker Compose service name (e.g., `sumeetsaini_com`, `vulkan_api`)
- Add IPv6 `listen [::]:80;` and `listen [::]:443 ssl http2;` if your domain has AAAA DNS record
- The ACME location block is **required** for automatic SSL renewal

#### 2. Add Upstream Definition (if needed)

If your backend is a separate service, ensure it's defined in `services/gateway/conf.d/prod/upstreams.conf`:

```nginx
upstream <your_backend_service_name>_prod {
    server <service_name>:<port>;
}
```

#### 3. Ensure Backend Service Exists

Add your application service to `docker-compose-prod.yml` if it's not already there.

#### 4. Deploy to Production

```bash
# Commit and push to trigger GitHub Actions deployment
git add .
git commit -m "Add <domain> to gateway"
git push origin main
```

#### 5. Obtain SSL Certificate

After deployment, SSH to your VPS and run:

```bash
cd /var/www/containers

# Create webroot directory (only needed once)
sudo mkdir -p /var/www/letsencrypt/.well-known/acme-challenge

# Request SSL certificate (adjust domains as needed)
sudo certbot certonly --webroot --webroot-path /var/www/letsencrypt -d <domain> -d www.<domain>

# The deploy hook will automatically restart nginx when certificate renews
```

#### 6. Verify

```bash
# Test renewal
sudo certbot renew --dry-run

# Check nginx is running
docker ps | grep gateway
```

### Notes

- **IPv6**: If your domain has an AAAA DNS record, nginx must listen on IPv6 (`[::]:80` and `[::]:443`). Otherwise Let's Encrypt's IPv6 validation will fail.
- **SSL Renewal Hook**: The hook at `/etc/letsencrypt/renewal-hooks/deploy/ssl-renewal-hook.sh` automatically restarts nginx when any gateway certificate renews.
- **Redundant Crontab**: If you see a crontab entry for `certbot renew`, remove it. The systemd timer (`certbot.timer`) handles automatic renewals.
- **Webroot Directory**: Must be mounted into the gateway container via `docker-compose-prod.yml` volume: `- /var/www/letsencrypt:/var/www/letsencrypt:ro`

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
