#!/bin/bash
set -e

echo "SSL renewal hook triggered for: $RENEWED_LINEAGE"

# Restart gateway nginx for renewed certificates
if [[ "$RENEWED_LINEAGE" == *"/sumeetsaini.com"* ]] || \
   [[ "$RENEWED_LINEAGE" == *"/vulkan.sumeetsaini.com"* ]]; then
    echo "Restarting gateway nginx for renewed SSL certificates..."
    docker restart gateway_nginx || echo "Warning: Could not restart gateway_nginx"
fi

# Handle mailcow SSL renewal
if [[ "$RENEWED_LINEAGE" == *"/sumeetsaini.com"* ]]; then
    echo "Updating mailcow SSL certificates..."
    if [ -d "/opt/mailcow-dockerized/data/assets/ssl" ]; then
        cp "$RENEWED_LINEAGE/fullchain.pem" /opt/mailcow-dockerized/data/assets/ssl/cert.pem
        cp "$RENEWED_LINEAGE/privkey.pem" /opt/mailcow-dockerized/data/assets/ssl/key.pem
        
        # Restart mailcow services that use SSL
        cd /opt/mailcow-dockerized
        docker compose restart nginx-mailcow postfix-mailcow dovecot-mailcow || echo "Warning: Could not restart mailcow SSL services"
    else
        echo "Warning: Mailcow SSL directory not found"
    fi
fi

echo "SSL renewal hook completed"