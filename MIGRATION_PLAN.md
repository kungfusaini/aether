# Aether Unified Docker Migration Plan

## Overview
Complete migration from fragmented setup to unified Docker Compose architecture with:
- Big bang migration approach
- SSL certificate consolidation
- No mailcow networking changes (host networking)
- Enhanced monitoring and health checks (later)
- Security enhancements (if needed)
- README update only
- Testing framework (later)

## Phase 1: Directory Structure Creation

### 1.1 Create New Directory Structure
```bash
mkdir -p services/gateway/sites/{dev,prod}
mkdir -p services/sumeetsaini_com
mkdir -p services/vulkan  
mkdir -p ssl
mkdir -p scripts
mkdir -p backups
```

### 1.2 Move Gateway Files
```bash
mv gateway/ services/
```

## Phase 2: Git Submodule Relocation

### 2.1 Update .gitmodules File
**Current:**
```gitmodules
[submodule "sumeetsaini_com"]
    path = sumeetsaini_com
    url = git@github.com:kungfusaini/sumeetsaini_com.git

[submodule "vulkan"]
    path = vulkan
    url = git@github.com:kungfusaini/vulkan.git
```

**New:**
```gitmodules
[submodule "services/sumeetsaini_com"]
    path = services/sumeetsaini_com
    url = git@github.com:kungfusaini/sumeetsaini_com.git

[submodule "services/vulkan"]
    path = services/vulkan
    url = git@github.com:kungfusaini/vulkan.git
```

### 2.2 Git Submodule Commands
```bash
# Remove old submodule references
git submodule deinit sumeetsaini_com
git submodule deinit vulkan
git rm sumeetsaini_com
git rm vulkan

# Add submodules at new locations
git submodule add git@github.com:kungfusaini/sumeetsaini_com.git services/sumeetsaini_com
git submodule add git@github.com:kungfusaini/vulkan.git services/vulkan
```

## Phase 3: Docker Compose Files

### 3.1 Base docker-compose.yml
```yaml
services:
  sumeetsaini_com:
    build: 
      context: ./services/sumeetsaini_com
      dockerfile: Dockerfile.dev
    restart: unless-stopped
    
  vulkan:
    build:
      context: ./services/vulkan  
      dockerfile: Dockerfile.dev
    environment:
      - NODE_ENV=dev
      - PORT=3000
      - HOST=0.0.0.0
      - MAIL_ENABLED=false
    restart: unless-stopped
    
  gateway:
    build:
      context: ./services/gateway
      dockerfile: Dockerfile
    ports:
      - "80:80"
      - "443:443"
    depends_on:
      - sumeetsaini_com
      - vulkan
    restart: unless-stopped
```

### 3.2 Development Override (docker-compose.dev.yml)
```yaml
services:
  sumeetsaini_com:
    volumes:
      - ./sumeetsaini_com:/app
      - /app/node_modules
    ports:
      - "8080:8080"
      
  vulkan:
    volumes:
      - ./vulkan:/app
      - /app/node_modules
    ports:
      - "3000:3000"
      
  gateway:
    volumes:
      - ./services/gateway/sites/dev:/etc/nginx/conf.d:ro
```

### 3.3 Production Override (docker-compose.prod.yml)
```yaml
services:
  sumeetsaini_com:
    build:
      dockerfile: Dockerfile.prod
    image: ghcr.io/kungfusaini/sumeetsaini_com:latest
    
  vulkan:
    build:
      dockerfile: Dockerfile.prod
    image: ghcr.io/kungfusaini/vulkan:latest
    environment:
      - NODE_ENV=prod
      - MAIL_ENABLED=true
      
  gateway:
    volumes:
      - /etc/letsencrypt:/etc/letsencrypt:ro
      - ./services/gateway/sites/prod:/etc/nginx/conf.d:ro
```

## Phase 4: Gateway Configuration

### 4.1 Gateway Dockerfile
```dockerfile
# services/gateway/Dockerfile
FROM nginx:stable-alpine
COPY nginx.conf /etc/nginx/nginx.conf
```

### 4.2 Gateway nginx.conf
```nginx
# services/gateway/nginx.conf
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log notice;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/conf.d/*.conf;
}
```

### 4.3 Development Sites

**services/gateway/sites/dev/localhost.conf:**
```nginx
upstream sumeetsaini_com {
    server sumeetsaini_com:8080;
}

upstream vulkan_api {
    server vulkan:3000;
}

server {
    listen 80;
    server_name localhost;
    
    location / {
        proxy_pass http://sumeetsaini_com/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /ws {
        proxy_pass http://sumeetsaini_com/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /vulkan/ {
        proxy_pass http://vulkan_api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name vulkan.localhost;
    
    location / {
        proxy_pass http://vulkan_api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 4.4 Production Sites

**services/gateway/sites/prod/sumeetsaini.com.conf:**
```nginx
upstream sumeetsaini_com {
    server sumeetsaini_com:80;
}

upstream vulkan_api {
    server vulkan:3000;
}

server {
    listen 80;
    server_name sumeetsaini.com www.sumeetsaini.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name sumeetsaini.com www.sumeetsaini.com;
    
    ssl_certificate /etc/letsencrypt/live/sumeetsaini.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sumeetsaini.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    
    location / {
        proxy_pass http://sumeetsaini_com;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /vulkan/ {
        valid_referers sumeetsaini.com *.sumeetsaini.com;
        if ($invalid_referer) {
            return 403;
        }
        proxy_pass http://vulkan_api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**services/gateway/sites/prod/vulkan.sumeetsaini.com.conf:**
```nginx
upstream vulkan_api {
    server vulkan:3000;
}

server {
    listen 80;
    server_name vulkan.sumeetsaini.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name vulkan.sumeetsaini.com;
    
    ssl_certificate /etc/letsencrypt/live/vulkan.sumeetsaini.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/vulkan.sumeetsaini.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    
    location / {
        proxy_pass http://vulkan_api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**services/gateway/sites/prod/mail.sumeetsaini.com.conf:**
```nginx
server {
    listen 80;
    server_name mail.sumeetsaini.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name mail.sumeetsaini.com;
    
    ssl_certificate /etc/letsencrypt/live/sumeetsaini.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sumeetsaini.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Phase 5: Mailcow Integration

### 5.1 docker-compose.mailcow.yml
Copy existing mailcow setup with minimal changes:
- Ensure nginx binds to 127.0.0.1:8080
- Keep all existing data volumes
- No networking changes (host networking)

## Phase 6: SSL Certificate Consolidation

### 6.1 SSL Certificate Strategy
- Consolidate to wildcard certificate: *.sumeetsaini.com
- Single certificate covers all subdomains
- Simplify SSL renewal and management

### 6.2 SSL Scripts

**scripts/ssl-renewal-hook.sh:**
```bash
#!/bin/bash
set -e

# Restart gateway nginx for renewed certificates
if [[ "$RENEWED_LINEAGE" == *"/sumeetsaini.com"* ]] || \
   [[ "$RENEWED_LINEAGE" == *"/vulkan.sumeetsaini.com"* ]]; then
    echo "Restarting gateway nginx for renewed SSL certificates..."
    docker restart gateway_nginx
fi

# Handle mailcow SSL renewal
if [[ "$RENEWED_LINEAGE" == *"/sumeetsaini.com"* ]]; then
    echo "Updating mailcow SSL certificates..."
    cp "$RENEWED_LINEAGE/fullchain.pem" /opt/mailcow-dockerized/data/assets/ssl/cert.pem
    cp "$RENEWED_LINEAGE/privkey.pem" /opt/mailcow-dockerized/data/assets/ssl/key.pem
    docker compose -f /opt/mailcow-dockerized/docker-compose.yml restart nginx-mailcow postfix-mailcow dovecot-mailcow
fi
```

**scripts/ssl-setup.sh:**
```bash
#!/bin/bash
set -e

# Copy SSL renewal hook
cp scripts/ssl-renewal-hook.sh /etc/letsencrypt/renewal-hooks/deploy/
chmod +x /etc/letsencrypt/renewal-hooks/deploy/ssl-renewal-hook.sh

echo "SSL setup complete"
```

## Phase 7: GitHub Actions Update

### 7.1 Enhanced .github/workflows/deploy.yml
```yaml
name: Deploy to Aether

on:
  push:
    branches: [ main ]

jobs:
  build-and-deploy:
    runs-on: ubuntu-24.04-arm
    permissions:
      contents: read
      packages: write

    steps:
    - name: Checkout code
      uses: actions/checkout@v4
      with:
        submodules: recursive

    - name: Log in to GitHub Container Registry
      uses: docker/login-action@v3
      with:
        registry: ghcr.io
        username: ${{ github.actor }}
        password: ${{ secrets.GITHUB_TOKEN }}

    - name: Build and push Vulkan image
      uses: docker/build-push-action@v5
      with:
        context: ./services/vulkan
        file: ./services/vulkan/Dockerfile.prod
        push: true
        platforms: linux/arm64
        tags: ghcr.io/kungfusaini/vulkan:latest

    - name: Build and push sumeetsaini_com image
      uses: docker/build-push-action@v5
      with:
        context: ./services/sumeetsaini_com
        file: ./services/sumeetsaini_com/Dockerfile.prod
        push: true
        platforms: linux/arm64
        tags: ghcr.io/kungfusaini/sumeetsaini_com:latest

    - name: Sync compose files to AETHER
      uses: appleboy/scp-action@v0.1.7
      with:
        host: ${{ secrets.AETHER_HOST }}
        username: ${{ secrets.AETHER_USER }}
        key: ${{ secrets.AETHER_SSH_KEY }}
        source: "docker-compose*.yml,services/,scripts/"
        target: ${{ secrets.AETHER_PATH }}
        strip_components: 0

    - name: Deploy to AETHER
      uses: appleboy/ssh-action@v1.0.3
      with:
        host: ${{ secrets.AETHER_HOST }}
        username: ${{ secrets.AETHER_USER }}
        key: ${{ secrets.AETHER_SSH_KEY }}
        script: |
          cd ${{ secrets.AETHER_PATH }}
          docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.mailcow.yml pull
          docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.mailcow.yml up -d
```

## Phase 8: README Update

### 8.1 README.md
```markdown
# Aether - Unified Docker Setup

## Development
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Access:
- Frontend: http://localhost
- API: http://vulkan.localhost

## Production Deployment
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.mailcow.yml up -d
```

Access:
- Frontend: https://sumeetsaini.com
- API: https://vulkan.sumeetsaini.com
- Mail: https://mail.sumeetsaini.com

## SSL Management
SSL certificates are automatically renewed via Let's Encrypt. Renewal hooks restart services as needed.

## Services
- **sumeetsaini_com**: Frontend with Three.js 3D graphics
- **vulkan**: Express.js API backend
- **gateway**: Nginx reverse proxy with SSL termination
- **mailcow**: Email service (production only)
```

## Phase 9: Migration Execution

### 9.1 Backup Current Setup
```bash
# Backup existing configurations
cp -r /var/www/containers/gateway backups/gateway-$(date +%Y%m%d)
cp -r /root/.aether-config backups/aether-config-$(date +%Y%m%d)
```

### 9.2 Stop Old Services
```bash
# Stop old gateway nginx
docker stop gateway_nginx
# Keep mailcow running (no changes needed)
```

### 9.3 Deploy New Structure
```bash
# Deploy new unified setup
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.mailcow.yml up -d
```

### 9.4 Test All Services
```bash
# Test all services are working
curl -I https://sumeetsaini.com
curl -I https://vulkan.sumeetsaini.com/status
curl -I https://mail.sumeetsaini.com
```

### 9.5 Cleanup Old Setup
```bash
# Remove old gateway container
docker rm gateway_nginx
# Keep mailcow as-is
```

## Phase 10: Security Enhancements

### 10.1 Security Headers Review
- Add security headers to nginx configurations
- Implement rate limiting if needed
- Review SSL configurations
- Add access logging

### 10.2 SSL Certificate Validation
- Verify all certificates are properly configured
- Test SSL renewal process
- Validate certificate chains
- Check for security vulnerabilities

## Final Structure
```
aether/
├── docker-compose.yml
├── docker-compose.dev.yml
├── docker-compose.prod.yml
├── docker-compose.mailcow.yml
├── services/
│   ├── gateway/
│   │   ├── Dockerfile
│   │   ├── nginx.conf
│   │   └── sites/
│   │       ├── dev/
│   │       │   ├── localhost.conf
│   │       │   └── vulkan.localhost.conf
│   │       └── prod/
│   │           ├── sumeetsaini.com.conf
│   │           ├── vulkan.sumeetsaini.com.conf
│   │           └── mail.sumeetsaini.com.conf
│   ├── sumeetsaini_com/
│   │   ├── Dockerfile.dev
│   │   └── Dockerfile.prod
│   └── vulkan/
│       ├── Dockerfile.dev
│       └── Dockerfile.prod
├── ssl/
├── scripts/
│   ├── ssl-renewal-hook.sh
│   └── ssl-setup.sh
├── backups/
└── README.md
```

## Migration Commands Summary

```bash
# 1. Create structure
mkdir -p services/gateway/sites/{dev,prod}
mkdir -p services/sumeetsaini_com
mkdir -p services/vulkan
mkdir -p ssl
mkdir -p scripts
mkdir -p backups

# 2. Move gateway
mv gateway/ services/

# 3. Update submodules
git submodule deinit sumeetsaini_com
git submodule deinit vulkan
git rm sumeetsaini_com
git rm vulkan
git submodule add git@github.com:kungfusaini/sumeetsaini_com.git services/sumeetsaini_com
git submodule add git@github.com:kungfusaini/vulkan.git services/vulkan

# 4. Backup
cp -r /var/www/containers/gateway backups/gateway-$(date +%Y%m%d)
cp -r /root/.aether-config backups/aether-config-$(date +%Y%m%d)

# 5. Deploy
docker stop gateway_nginx
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.mailcow.yml up -d

# 6. Test
curl -I https://sumeetsaini.com
curl -I https://vulkan.sumeetsaini.com/status
curl -I https://mail.sumeetsaini.com
```

This plan provides complete migration to unified Docker setup while maintaining all existing functionality and following your requirements.