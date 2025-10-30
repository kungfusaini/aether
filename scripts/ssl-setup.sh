#!/bin/bash
set -e

echo "Setting up SSL management..."

# Create renewal hooks directory if it doesn't exist
sudo mkdir -p /etc/letsencrypt/renewal-hooks/deploy

# Copy SSL renewal hook
sudo cp scripts/ssl-renewal-hook.sh /etc/letsencrypt/renewal-hooks/deploy/
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/ssl-renewal-hook.sh

echo "SSL setup complete"
echo "Renewal hook installed at: /etc/letsencrypt/renewal-hooks/deploy/ssl-renewal-hook.sh"