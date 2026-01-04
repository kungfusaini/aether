#!/bin/bash

# Setup script for Vulkan Data Backup Repository
# This script will help set up the private GitHub repository and initial backup

set -e

echo "🚀 Setting up Vulkan Data Backup Repository..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
REPO_NAME="vulkan-data"
REPO_URL="git@github.com:kungfusaini/$REPO_NAME.git"
TEMP_DIR="/tmp/$REPO_NAME-setup"
SOURCE_DIR="./aether/services/vulkan/data"

# Check if source data directory exists
if [ ! -d "$SOURCE_DIR" ]; then
    echo -e "${RED}❌ Source data directory not found: $SOURCE_DIR${NC}"
    exit 1
fi

echo -e "${YELLOW}📁 Source data directory: $SOURCE_DIR${NC}"
ls -la "$SOURCE_DIR"

# Check if SSH key already exists
SSH_KEY_PATH="$HOME/.ssh/vulkan-backup"
if [ -f "$SSH_KEY_PATH" ]; then
    echo -e "${YELLOW}🔑 SSH key already exists at $SSH_KEY_PATH${NC}"
    echo -e "${YELLOW}⚠️  If you want to generate a new key, delete the existing one first${NC}"
else
    echo -e "${GREEN}🔑 Generating SSH deploy key...${NC}"
    ssh-keygen -t ed25519 -C "vulkan-backup@production" -f "$SSH_KEY_PATH" -N ""
    
    echo -e "${GREEN}✅ SSH key generated successfully${NC}"
    echo -e "${YELLOW}📋 Public key (add this to GitHub deploy keys):${NC}"
    cat "$SSH_KEY_PATH.pub"
    
    echo ""
    echo -e "${YELLOW}🔗 Instructions:${NC}"
    echo "1. Go to: https://github.com/kungfusaini/vulkan-data/settings/keys"
    echo "2. Click 'Add deploy key'"
    echo "3. Title: 'Vulkan Production Backup'"
    echo "4. Key: Copy the public key above"
    echo "5. ✅ Allow write access"
    echo "6. Click 'Add deploy key'"
    echo ""
    read -p "Press Enter after you've added the deploy key to GitHub..."
fi

# Check if repository is accessible
echo -e "${GREEN}🔍 Testing repository access...${NC}"
if ssh -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
    echo -e "${GREEN}✅ GitHub SSH access working${NC}"
else
    echo -e "${RED}❌ GitHub SSH access failed${NC}"
    echo -e "${YELLOW}Please check your SSH key and GitHub deploy key setup${NC}"
    exit 1
fi

# Clone or create repository
echo -e "${GREEN}📥 Setting up repository...${NC}"
if [ -d "$TEMP_DIR" ]; then
    rm -rf "$TEMP_DIR"
fi

# Clone the repository
git clone "$REPO_URL" "$TEMP_DIR"

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Failed to clone repository${NC}"
    echo -e "${YELLOW}Make sure the repository exists and you have access${NC}"
    exit 1
fi

# Copy current data files
echo -e "${GREEN}📋 Copying current data files...${NC}"
cp -r "$SOURCE_DIR"/* "$TEMP_DIR/"

cd "$TEMP_DIR"

# Configure git
git config user.name "Vulkan Backup Bot"
git config user.email "backup@vulkan.sumeetsaini.com"

# Initial commit
echo -e "${GREEN}💾 Creating initial backup commit...${NC}"
git add .
git commit -m "Initial backup: Setup - $(date '+%Y-%m-%d %H:%M:%S')"

# Push to repository
echo -e "${GREEN}📤 Pushing to GitHub...${NC}"
git push origin main

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Initial backup completed successfully!${NC}"
else
    echo -e "${RED}❌ Failed to push to GitHub${NC}"
    exit 1
fi

# Setup production SSH key
echo -e "${GREEN}🔧 Setting up production SSH key...${NC}"
SECRETS_DIR="./aether/services/vulkan/secrets"
mkdir -p "$SECRETS_DIR"

# Copy private key to secrets directory (without .pub extension)
cp "$SSH_KEY_PATH" "$SECRETS_DIR/backup_ssh_key"
chmod 600 "$SECRETS_DIR/backup_ssh_key"

echo -e "${GREEN}✅ Production SSH key set up${NC}"

# Clean up
cd /
rm -rf "$TEMP_DIR"

echo ""
echo -e "${GREEN}🎉 Setup completed successfully!${NC}"
echo ""
echo -e "${YELLOW}📋 Summary:${NC}"
echo "✅ Private repository: $REPO_URL"
echo "✅ SSH deploy key configured"
echo "✅ Initial backup created"
echo "✅ Production SSH key placed in: $SECRETS_DIR/backup_ssh_key"
echo ""
echo -e "${YELLOW}🚀 Next steps:${NC}"
echo "1. Update GitHub Actions with VULKAN_BACKUP_SSH_KEY secret"
echo "2. Deploy your application via GitHub Actions"
echo "3. Test the backup functionality by calling API endpoints"
echo ""
echo -e "${YELLOW}🔍 To test locally:${NC}"
echo "cd ./aether/services/vulkan && npm install && npm run dev"
echo ""
echo -e "${YELLOW}📖 To view backup history:${NC}"
echo "git clone $REPO_URL && cd $REPO_NAME && git log --oneline"