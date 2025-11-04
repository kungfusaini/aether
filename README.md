# Aether - Unified Docker Setup

Complete Docker Compose orchestration for web applications with integrated services.

## Architecture

- **sumeetsaini_com**: [Frontend](https://github.com/kungfusaini/sumeetsaini_com) web application
- **vulkan**: [Backend](https://github.com/kungfusaini/vulkan) API service  
- **gateway**: Nginx reverse proxy with SSL termination
- **mailcow**: Email service (production only)

## Development

```bash
# Start development environment
docker compose -f docker-compose.yml -f docker-compose-dev.yml up -d

# View logs
docker compose logs -f

# Stop services
docker compose down
```

**Access:**
- Frontend: http://localhost
- API: http://vulkan.localhost

**Email:** Enabled by default with auto-generated Ethereal credentials for testing.

## Production Deployment

```bash
# Deploy all services
docker compose -f docker-compose.yml -f docker-compose-prod.yml -f docker-compose-mailcow.yml up -d
```

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
- Rate-limited contact system
- Security-hardened services
- Containerized deployment
- Unified Docker orchestration
- SSL certificate management
- Integrated email service
