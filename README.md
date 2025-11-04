# Aether - Unified Docker Setup

Complete Docker Compose orchestration for web applications with integrated services.

## Architecture

- **sumeetsaini_com**: [Frontend](https://github.com/kungfusaini/sumeetsaini_com) web application
- **vulkan**: [Backend](https://github.com/kungfusaini/vulkan) API service  
- **gateway**: Nginx reverse proxy with SSL termination
- **mailcow**: Email service (production only, host networking)

## Development

### Running Services

```bash
# Start development environment (mail enabled by default)
docker compose -f docker-compose.yml -f docker-compose-dev.yml up -d

# View logs
docker compose logs -f

# Stop services
docker compose down
```

### Email Configuration

- **Mail is ENABLED BY DEFAULT** in development
- **CONTACT_EMAIL** uses hardcoded `dev-test@example.com` for testing
- **Ethereal credentials** auto-generated on container startup for testing
- **To disable mail**: Set `MAIL_ENABLED=false` in `docker-compose-dev.yml`
- **ZERO setup required** - just run and go!

### How Ethereal Setup Works

Instead of using init containers (which caused complexity), the application now:

1. **Auto-generates Ethereal credentials** when `NODE_ENV=dev` and `MAIL_ENABLED=true`
2. **Sets credentials as environment variables** directly in the running container
3. **No file I/O or volume sharing needed** - credentials are available immediately
4. **Fresh credentials each container restart** for security

This approach eliminates init container complexity while maintaining the same functionality.

Access:
- Frontend: http://localhost
- API: http://vulkan.localhost

## Production Deployment

```bash
# Deploy all services including mailcow
docker compose -f docker-compose.yml -f docker-compose-prod.yml -f docker-compose-mailcow.yml up -d

# Deploy without mailcow (for testing)
docker compose -f docker-compose.yml -f docker-compose-prod.yml up -d
```

## SSL Management

SSL certificates are automatically managed via Let's Encrypt with wildcard certificate `*.sumeetsaini.com`.

### Setup Wildcard Certificate

```bash
# Run on server
./scripts/wildcard-cert-setup.sh

# Install renewal hooks
./scripts/ssl-setup.sh
```

### Certificate Renewal

Certificates auto-renew via cron. Renewal hooks automatically restart services:
- Gateway nginx restarts for SSL changes
- Mailcow SSL certificates are updated and relevant services restart

## Directory Structure

```
aether/
├── docker-compose.yml              # Base service definitions
├── docker-compose-dev.yml          # Development overrides
├── docker-compose-prod.yml         # Production overrides  
├── docker-compose-mailcow.yml      # Mailcow integration
├── services/
│   ├── gateway/                    # Nginx reverse proxy
│   │   ├── Dockerfile
│   │   ├── nginx.conf
│   │   └── sites/                  # Nginx configurations
│   │       ├── dev/                # Development configs
│   │       └── prod/               # Production configs
│   ├── sumeetsaini_com/            # Frontend service
│   └── vulkan/                     # Backend service
├── scripts/                        # Management scripts
│   ├── ssl-renewal-hook.sh         # SSL renewal automation
│   ├── ssl-setup.sh               # SSL hook installation
│   └── wildcard-cert-setup.sh     # Wildcard certificate setup
└── backups/                        # Backup storage
```

## Mailcow Integration

Mailcow runs with host networking for compatibility but is orchestrated through Docker Compose:

- **Preserves existing mailcow setup** - No data migration required
- **Unified management** - Start/stop with other services
- **SSL integration** - Uses wildcard certificate via renewal hooks
- **Host networking** - Maintains existing port bindings and email functionality

## Deployment Pipeline

Automated via GitHub Actions:
1. Builds and pushes Docker images to GHCR
2. Syncs compose files and configurations to server
3. Deploys services with proper orchestration
4. Runs health checks on all endpoints
5. Cleans up old Docker images

### GitHub Actions Workflow

The deployment pipeline is triggered on:
- **Push to main branch**: Automatic production deployment
- **Manual workflow trigger**: On-demand deployment

**Pipeline Steps:**
1. **Build Phase**: Multi-platform Docker builds (linux/arm64)
2. **Registry Push**: Images pushed to GitHub Container Registry
3. **File Sync**: Configuration files synced to production server
4. **Service Deployment**: Docker Compose orchestration with secrets
5. **Health Verification**: Endpoint health checks and service validation
6. **Cleanup**: Automated Docker image pruning

**Security:**
- All secrets managed via GitHub Secrets
- No sensitive data in repository
- Automated credential rotation support

## Environment Variables

### Development
- `NODE_ENV=dev`
- `MAIL_ENABLED=true` (default, can be disabled by setting `MAIL_ENABLED=false` in `docker-compose-dev.yml`)
- Volume mounts for live reloading
- `CONTACT_EMAIL` uses hardcoded `dev-test@example.com` for testing
- Auto-generated Ethereal credentials for testing

### Production
- `NODE_ENV=prod` 
- `MAIL_ENABLED=true`
- `CONTACT_EMAIL` from GitHub Secrets
- `MAILCOW_HOST=mailcowdockerized-postfix-mailcow-1` (public container name)
- Pre-built images from GHCR
- SSL certificate mounts

### Secrets Management

**Local Development:**
- **ZERO setup required** - uses hardcoded `dev-test@example.com`
- **To use real email**: Edit `docker-compose-dev.yml` and change `CONTACT_EMAIL`

**Production:**
- `CONTACT_EMAIL` injected directly from GitHub Secrets (no files on disk)
- `MAILCOW_HOST` is public configuration in docker-compose-prod.yml



### Contact Form Email Flow

**Development:**
- Emails sent to your `CONTACT_EMAIL` via Ethereal (test email service)
- View test emails at Ethereal web interface (shown in logs)

**Production:**
- Emails sent to your `CONTACT_EMAIL` via Mailcow SMTP
- Uses internal Docker container: `mailcowdockerized-postfix-mailcow-1`

## Features

- Modern web application frontend
- RESTful API backend
- Rate-limited contact system
- Security-hardened services
- Containerized deployment
- Unified Docker orchestration
- SSL certificate management
- Integrated email service
