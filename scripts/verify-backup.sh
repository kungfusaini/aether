#!/bin/bash

# Final verification script for Vulkan Data Backup implementation
# This script verifies all components are properly configured

set -e

echo "🔍 Verifying Vulkan Data Backup Implementation..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Track overall status
TOTAL_CHECKS=0
PASSED_CHECKS=0

# Helper function to run checks
check() {
    local description=$1
    local test_command=$2
    local importance=${3:-"required"}
    
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    echo -n "  $description ... "
    
    if eval "$test_command" >/dev/null 2>&1; then
        echo -e "${GREEN}✅ PASS${NC}"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
        return 0
    else
        if [ "$importance" = "optional" ]; then
            echo -e "${YELLOW}⚠️  OPTIONAL (not configured)${NC}"
            return 0
        else
            echo -e "${RED}❌ FAIL${NC}"
            return 1
        fi
    fi
}

echo -e "${BLUE}📁 File Structure Checks${NC}"
echo "Checking if all backup-related files exist..."

check "backup-manager.js" "test -f ./aether/services/vulkan/src/utils/backup-manager.js"
check "backup-middleware.js" "test -f ./aether/services/vulkan/src/middleware/backup-middleware.js"
check "Setup script" "test -f ./aether/scripts/setup-backup.sh"
check "Test script" "test -f ./aether/scripts/test-backup.sh"
check "Docker secrets dir" "test -d ./aether/services/vulkan/secrets"

echo ""
echo -e "${BLUE}🔧 Code Integration Checks${NC}"
echo "Checking if backup middleware is properly integrated..."

check "vault.js has backup import" "grep -q 'backupMiddleware' ./aether/services/vulkan/src/routes/vault.js"
check "well.js has backup import" "grep -q 'backupMiddleware' ./aether/services/vulkan/src/routes/well.js"
check "vault.js backup on POST spend" "grep -q 'backupMiddleware.*POST /spend' ./aether/services/vulkan/src/routes/vault.js"
check "vault.js backup on PUT data" "grep -q 'backupMiddleware.*PUT /data' ./aether/services/vulkan/src/routes/vault.js"
check "vault.js backup on POST income" "grep -q 'backupMiddleware.*POST /income' ./aether/services/vulkan/src/routes/vault.js"
check "well.js backup on POST" "grep -q 'backupMiddleware.*POST /well' ./aether/services/vulkan/src/routes/well.js"

echo ""
echo -e "${BLUE}📦 Package Dependencies${NC}"
echo "Checking required packages..."

check "simple-git in package.json" "grep -q 'simple-git' ./aether/services/vulkan/package.json"

echo ""
echo -e "${BLUE}🐳 Docker Configuration${NC}"
echo "Checking Docker setup..."

check "Dockerfile.prod exists" "test -f ./aether/services/vulkan/Dockerfile.prod"
check "Dockerfile has git" "grep -q 'git\|openssh' ./aether/services/vulkan/Dockerfile.prod"
check "docker-compose-prod.yml exists" "test -f ./aether/docker-compose-prod.yml"
check "Backup repo URL configured" "grep -q 'BACKUP_REPO_URL' ./aether/docker-compose-prod.yml"
check "Backup secret configured" "grep -q 'backup_ssh_key' ./aether/docker-compose-prod.yml"
check "Backup volume configured" "grep -q 'vulkan_data_backup' ./aether/docker-compose-prod.yml"

echo ""
echo -e "${BLUE}🚀 GitHub Actions${NC}"
echo "Checking CI/CD configuration..."

check "Deploy workflow exists" "test -f ./aether/.github/workflows/deploy.yml"
check "Backup secret in workflow" "grep -q 'VULKAN_BACKUP_SSH_KEY' ./aether/.github/workflows/deploy.yml"
check "SSH key setup in workflow" "grep -q 'backup_ssh_key.*EOF' ./aether/.github/workflows/deploy.yml"

echo ""
echo -e "${BLUE}🔐 Security & Setup${NC}"
echo "Checking security configurations..."

check "app.js initializes backup" "grep -q 'backupManager.initialize' ./aether/services/vulkan/src/app.js"
check "Scripts are executable" "test -x ./aether/scripts/setup-backup.sh && test -x ./aether/scripts/test-backup.sh"
check "Secrets README exists" "test -f ./aether/services/vulkan/secrets/README.md"

echo ""
echo -e "${BLUE}📋 Data Directory${NC}"
echo "Checking data directory structure..."

check "Data directory exists" "test -d ./aether/services/vulkan/data"
check "categories.json exists" "test -f ./aether/services/vulkan/data/categories.json"
check "financial_data.csv exists" "test -f ./aether/services/vulkan/data/financial_data.csv"
check "notes.md exists" "test -f ./aether/services/vulkan/data/notes.md"

echo ""
echo -e "${BLUE}🎯 Final Status${NC}"
echo "Implementation verification complete!"

if [ $PASSED_CHECKS -eq $TOTAL_CHECKS ]; then
    echo -e "${GREEN}🎉 ALL CHECKS PASSED!${NC}"
    echo ""
    echo -e "${GREEN}✅ Your Vulkan Data Backup system is ready for deployment!${NC}"
    echo ""
    echo -e "${YELLOW}📋 Next steps:${NC}"
    echo "1. Run setup script: ./aether/scripts/setup-backup.sh"
    echo "2. Add VULKAN_BACKUP_SSH_KEY to GitHub secrets"
    echo "3. Deploy: git push to main branch"
    echo "4. Test production API calls"
    echo ""
    echo -e "${YELLOW}📖 To monitor backups:${NC}"
    echo "git clone git@github.com:kungfusaini/vulkan-data.git"
    echo "cd vulkan-data && git log --oneline -f"
else
    echo -e "${RED}❌ $PASSED_CHECKS/$TOTAL_CHECKS checks passed${NC}"
    echo ""
    echo -e "${YELLOW}🔧 Please fix the failed checks before deployment${NC}"
    echo ""
    echo -e "${YELLOW}📖 For help:${NC}"
    echo "1. Run test script: ./aether/scripts/test-backup.sh"
    echo "2. Check the setup script: ./aether/scripts/setup-backup.sh"
    echo "3. Review the implementation files"
    exit 1
fi

echo ""
echo -e "${BLUE}📚 Documentation Reference${NC}"
echo "• Backup trigger: Any PUT/POST to vault or well endpoints"
echo "• Backup location: Private repo git@github.com:kungfusaini/vulkan-data.git"
echo "• Rollback: Manual git checkout in /app/data-backup"
echo "• Monitoring: Git log provides complete backup history"