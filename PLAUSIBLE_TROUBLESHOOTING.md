# Plausible Analytics Deployment Troubleshooting Guide

## 🚨 Current Issue

Plausible Analytics deployment is failing due to configuration file synchronization problems between GitHub Actions and production server.

## 🔍 Root Cause Analysis

### The Problem
Gateway container is using outdated configuration files instead of the new Plausible configurations that were built into the Docker image.

### What Should Happen
1. GitHub Actions builds gateway image with Plausible configs ✅
2. Container starts and uses baked-in configs ✅
3. Nginx routes `stats.sumeetsaini.com` to Plausible service ✅

### What's Actually Happening
1. Gateway container uses old VPS config files ❌
2. Missing `stats.sumeetsaini.com.conf` configuration ❌
3. Missing `plausible_prod` upstream definition ❌
4. Nginx falls back to mail configuration ❌
5. SSL certificate mismatch errors ❌

## 🔧 Solutions Implemented

### 1. Fixed Docker Compose Configuration
- **File:** `docker-compose-prod.yml`
- **Change:** Removed problematic volume mount that was overriding baked-in configs
- **Result:** Container now uses configurations baked into image

### 2. Updated GitHub Actions Workflow
- **File:** `.github/workflows/deploy.yml`
- **Change:** Removed `services/gateway/conf.d/` from source path
- **Result:** No more config file sync conflicts

### 3. Development Environment Setup
- **File:** `docker-compose-dev.yml`
- **Change:** Added config volume mount for development flexibility
- **Result:** Quick config changes without rebuilding images

## 🚀 Current Status

### ✅ What's Working
- All Plausible containers are running
- Gateway container is using correct image
- SSL certificates exist and are valid
- Volume mounts are working correctly

### ❌ What Needs Fixing
- VPS still has outdated config files from November 5th
- GitHub Actions deployment needs to sync updated config files
- Container restart required to pick up new configurations

## 📋 Required Actions

### For Production Deployment
1. **Deploy latest changes via GitHub Actions**
   - Push changes to trigger new deployment
   - Monitor deployment logs for success
   - Verify gateway container restart

### For Local Development
1. **Start development environment**
   ```bash
   cd /var/www/containers
   docker compose -f docker-compose.yml -f docker-compose-dev.yml up -d
   ```

2. **Verify Plausible functionality**
   ```bash
   # Add to /etc/hosts
   echo "127.0.0.1 stats.localhost" | sudo tee -a /etc/hosts
   
   # Test access
   curl http://stats.localhost
   ```

## 🔍 Verification Commands

### Check Configuration Files
```bash
# Verify VPS has latest configs
ssh aether "ls -la /var/www/containers/services/gateway/conf.d/prod/"

# Verify container has correct configs
docker exec gateway_nginx ls -la /etc/nginx/conf.d/prod/"

# Check for stats config
docker exec gateway_nginx find /etc/nginx/conf.d/prod -name 'stats.sumeetsaini.com.conf'

# Verify upstream configuration
docker exec gateway_nginx cat /etc/nginx/conf.d/prod/upstreams.conf | grep plausible
```

### Test Plausible Analytics
```bash
# Test HTTP to HTTPS redirect
curl -I http://stats.sumeetsaini.com

# Test HTTPS access
curl -I https://stats.sumeetsaini.com

# Check Nginx configuration
docker exec gateway_nginx nginx -t

# Check Plausible service status
docker compose -f docker-compose.yml -f docker-compose-prod.yml -f docker-compose-plausible.yml logs plausible
```

## 🎯 Expected Results

After successful deployment:
- ✅ `stats.sumeetsaini.com` loads Plausible Analytics interface
- ✅ SSL certificate works without errors
- ✅ All upstream configurations include `plausible_prod`
- ✅ No Nginx configuration conflicts
- ✅ Plausible containers are healthy and communicating

## 📞 Common Issues & Solutions

### Issue: SSL Certificate Errors
**Symptom:** `ERR_TLS_CERT_ALTNAME_INVALID`
**Cause:** Wrong SSL certificate mounted or missing certificate
**Solution:** Ensure `/etc/letsencrypt/live/stats.sumeetsaini.com/` exists

### Issue: Container Won't Start
**Symptom:** `OCI runtime create failed`
**Cause:** Volume mount conflicts or missing files
**Solution:** Check volume mount paths and file permissions

### Issue: Configuration Not Loading
**Symptom:** Old configuration still active after restart
**Cause:** Docker volume mount overrides baked-in configs
**Solution:** Remove conflicting volume mounts or restart with `--force-recreate`

## 🔄 Maintenance Commands

### Update SSL Certificates
```bash
sudo certbot certonly --standalone -d stats.sumeetsaini.com
```

### Force Container Recreation
```bash
docker compose -f docker-compose.yml -f docker-compose-prod.yml -f docker-compose-plausible.yml up -d --force-recreate gateway
```

### Clear Docker Cache
```bash
docker system prune -f
docker compose down
docker compose up -d
```

## 📞 Support Information

### Log Locations
- **GitHub Actions:** https://github.com/kungfusaini/aether/actions
- **Production Logs:** `ssh aether "docker compose logs -f docker-compose-plausible.yml plausible"`
- **Nginx Logs:** `ssh aether "docker logs gateway_nginx"`

### Configuration Files
- **Production:** `/var/www/containers/services/gateway/conf.d/prod/`
- **Development:** `/var/www/containers/services/gateway/conf.d/dev/`
- **Docker Compose:** `/var/www/containers/docker-compose*.yml`

---

*Last Updated: 2025-12-03*