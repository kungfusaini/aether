#!/bin/bash
set -e

DOMAIN="sumeetsaini.com"
EMAIL="admin@sumeetsaini.com"  # Update this with your actual email

echo "Setting up wildcard SSL certificate for *.$DOMAIN..."

# Request wildcard certificate
sudo certbot certonly \
    --manual \
    --preferred-challenges dns \
    --email "$EMAIL" \
    --agree-tos \
    --manual-public-ip-logging-ok \
    -d "*.$DOMAIN" \
    -d "$DOMAIN"

echo "Wildcard certificate obtained!"
echo "Make sure to create TXT records for DNS challenge as prompted by certbot."

# Test renewal
echo "Testing renewal process..."
sudo certbot renew --dry-run

echo "Wildcard SSL setup complete!"