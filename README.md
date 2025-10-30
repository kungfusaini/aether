# Aether - Unified Docker Setup

Complete Docker Compose orchestration for sumeetsaini.com with integrated mailcow email service.

## Architecture

- **sumeetsaini_com**: Frontend with Three.js 3D graphics
- **vulkan**: Express.js API backend  
- **gateway**: Nginx reverse proxy with SSL termination
- **mailcow**: Email service (production only, host networking)

## Development

```bash
# Start development environment
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# View logs
docker compose logs -f

# Stop services
docker compose down
```

Access:
- Frontend: http://localhost
- API: http://vulkan.localhost

## Production Deployment

```bash
# Deploy all services including mailcow
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.mailcow.yml up -d

# Deploy without mailcow (for testing)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Access:
- Frontend: https://sumeetsaini.com
- API: https://vulkan.sumeetsaini.com  
- Mail: https://mail.sumeetsaini.com

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
├── docker-compose.dev.yml          # Development overrides
├── docker-compose.prod.yml         # Production overrides  
├── docker-compose.mailcow.yml      # Mailcow integration
├── services/
│   ├── gateway/                    # Nginx reverse proxy
│   │   ├── Dockerfile
│   │   ├── nginx.conf
│   │   └── sites/                  # Nginx configurations
│   │       ├── dev/                # Development configs
│   │       └── prod/               # Production configs
│   ├── sumeetsaini_com/            # Frontend submodule
│   └── vulkan/                     # API submodule
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

## Environment Variables

Development:
- `NODE_ENV=dev`
- `MAIL_ENABLED=false`
- Volume mounts for live reloading

Production:
- `NODE_ENV=prod` 
- `MAIL_ENABLED=true`
- Pre-built images from GHCR
- SSL certificate mounts

## API Endpoints

- `GET /status` - Health check
- `POST /web_contact` - Contact form (rate limited: 5/15min)

## Features

- Interactive 3D shape animations
- Responsive design
- Rate-limited contact API
- Security-hardened backend
- Containerized deployment
- Unified Docker orchestration
- Wildcard SSL certificate management
- Integrated mailcow email service