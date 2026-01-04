#!/bin/bash

# Test script for Vulkan Data Backup functionality
# This script tests the backup system locally before deployment

set -e

echo "🧪 Testing Vulkan Data Backup System..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
VULKAN_DIR="./aether/services/vulkan"
TEST_API_URL="http://localhost:3000"
API_KEY="test-key"  # You may need to get the actual API key

echo -e "${YELLOW}📁 Working directory: $(pwd)${NC}"

# Check if we're in the right directory
if [ ! -d "$VULKAN_DIR" ]; then
    echo -e "${RED}❌ Vulkan directory not found: $VULKAN_DIR${NC}"
    exit 1
fi

cd "$VULKAN_DIR"

# Check if dependencies are installed
echo -e "${GREEN}📦 Checking dependencies...${NC}"
if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}📥 Installing dependencies...${NC}"
    npm install
fi

# Check if backup files exist
echo -e "${GREEN}📋 Checking backup files...${NC}"
if [ ! -f "src/utils/backup-manager.js" ]; then
    echo -e "${RED}❌ backup-manager.js not found${NC}"
    exit 1
fi

if [ ! -f "src/middleware/backup-middleware.js" ]; then
    echo -e "${RED}❌ backup-middleware.js not found${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Backup files found${NC}"

# Test backup manager initialization (without actual backup)
echo -e "${GREEN}🔧 Testing backup manager initialization...${NC}"
node -e "
const backupManager = require('./src/utils/backup-manager');
backupManager.initialize()
  .then(() => console.log('✅ Backup manager initialized successfully'))
  .catch(err => console.log('❌ Backup manager initialization failed:', err.message));
"

echo ""

# Check if data directory exists
if [ ! -d "data" ]; then
    echo -e "${RED}❌ Data directory not found${NC}"
    exit 1
fi

echo -e "${YELLOW}📁 Current data files:${NC}"
ls -la data/

echo ""

# Test application startup
echo -e "${GREEN}🚀 Testing application startup...${NC}"
echo -e "${YELLOW}Starting vulkan app (will run for 5 seconds)...${NC}"

# Start app in background
npm run dev > /dev/null 2>&1 &
APP_PID=$!

# Wait for app to start
sleep 3

# Check if app is running
if curl -s "$TEST_API_URL" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Application is running${NC}"
else
    echo -e "${RED}❌ Application failed to start${NC}"
    kill $APP_PID 2>/dev/null || true
    exit 1
fi

# Test a simple API call
echo -e "${GREEN}📡 Testing API endpoint...${NC}"

# Test the vault test endpoint first
RESPONSE=$(curl -s -w "%{http_code}" "$TEST_API_URL/vault/test")
HTTP_CODE="${RESPONSE: -3}"

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✅ API test endpoint working${NC}"
else
    echo -e "${RED}❌ API test endpoint failed (HTTP $HTTP_CODE)${NC}"
fi

# Test health endpoint if it exists
curl -s "$TEST_API_URL/health" > /dev/null 2>&1 && echo -e "${GREEN}✅ Health endpoint working${NC}" || echo -e "${YELLOW}⚠️  No health endpoint found${NC}"

# Stop the app
kill $APP_PID 2>/dev/null || true
wait $APP_PID 2>/dev/null || true

echo ""

# Check docker files
echo -e "${GREEN}🐳 Checking Docker configuration...${NC}"
if [ -f "Dockerfile.prod" ]; then
    echo -e "${GREEN}✅ Dockerfile.prod found${NC}"
    
    # Check if git and openssh are included
    if grep -q "git\|openssh" "Dockerfile.prod"; then
        echo -e "${GREEN}✅ Dockerfile includes git/ssh dependencies${NC}"
    else
        echo -e "${RED}❌ Dockerfile missing git/ssh dependencies${NC}"
    fi
else
    echo -e "${RED}❌ Dockerfile.prod not found${NC}"
fi

echo ""

# Check compose files
echo -e "${GREEN}📋 Checking docker-compose configuration...${NC}"
if [ -f "../docker-compose-prod.yml" ]; then
    echo -e "${GREEN}✅ docker-compose-prod.yml found${NC}"
    
    # Check for backup configuration
    if grep -q "BACKUP_REPO_URL\|backup_ssh_key" "../docker-compose-prod.yml"; then
        echo -e "${GREEN}✅ Docker compose includes backup configuration${NC}"
    else
        echo -e "${RED}❌ Docker compose missing backup configuration${NC}"
    fi
    
    # Check for backup volume
    if grep -q "vulkan_data_backup" "../docker-compose-prod.yml"; then
        echo -e "${GREEN}✅ Backup volume configured${NC}"
    else
        echo -e "${RED}❌ Backup volume not found${NC}"
    fi
else
    echo -e "${RED}❌ docker-compose-prod.yml not found${NC}"
fi

echo ""

# Check secrets directory
echo -e "${GREEN}🔐 Checking secrets setup...${NC}"
if [ -d "secrets" ]; then
    echo -e "${GREEN}✅ Secrets directory exists${NC}"
    
    if [ -f "secrets/backup_ssh_key" ]; then
        echo -e "${GREEN}✅ Backup SSH key file exists${NC}"
        if [ -s "secrets/backup_ssh_key" ]; then
            echo -e "${GREEN}✅ SSH key file is not empty${NC}"
        else
            echo -e "${YELLOW}⚠️  SSH key file is empty${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  Backup SSH key file not found (run setup script)${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  Secrets directory not found${NC}"
fi

echo ""

# Summary
echo -e "${GREEN}🎉 Local testing completed!${NC}"
echo ""
echo -e "${YELLOW}📋 Next steps:${NC}"
echo "1. Run: ./scripts/setup-backup.sh"
echo "2. Update GitHub Actions with VULKAN_BACKUP_SSH_KEY secret"
echo "3. Deploy via: npm run build && git push"
echo "4. Test production by calling API endpoints"
echo ""
echo -e "${YELLOW}📖 To check backup history:${NC}"
echo "git clone git@github.com:kungfusaini/vulkan-data.git && cd vulkan-data && git log --oneline"
echo ""
echo -e "${YELLOW}🔄 To test rollback in production:${NC}"
echo "docker exec -it vulkan bash"
echo "cd /app/data-backup && git log --oneline -5"
echo "git checkout <commit-hash>"
echo "cp * /app/data/"
echo "docker restart vulkan"