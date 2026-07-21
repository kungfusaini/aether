# TLS certificate operations

Aether terminates HTTPS at the Nginx gateway and mounts certificates from the host's `/etc/letsencrypt` directory as read-only data.

## Renewal flow

1. Certbot writes HTTP-01 challenge files under `/var/www/letsencrypt/.well-known/acme-challenge/`.
2. The gateway serves that path over HTTP before applying HTTPS redirects.
3. The host's Certbot systemd timer checks for renewals.
4. `scripts/ssl-renewal-hook.sh` restarts the gateway after a certificate changes.

## Add a website

### 1. Prepare the challenge directory

```bash
sudo mkdir -p /var/www/letsencrypt/.well-known/acme-challenge
```

### 2. Request the certificate

Obtain the certificate before adding an HTTPS server block. Nginx will fail to start if its configuration references certificate files that do not exist.

```bash
sudo certbot certonly \
  --webroot \
  --webroot-path /var/www/letsencrypt \
  -d example.com \
  -d www.example.com
```

### 3. Add the gateway configuration

Create `services/gateway/conf.d/prod/example.com.conf`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name example.com www.example.com;

    location /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name example.com www.example.com;

    include /etc/nginx/conf.d/common/security.conf;

    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    location / {
        proxy_pass http://example_service_prod;
        include /etc/nginx/conf.d/common/proxy-headers.conf;
    }
}
```

Add the corresponding upstream when the service does not already have one:

```nginx
upstream example_service_prod {
    server example_service:8080;
}
```

### 4. Validate and deploy

Validate the Nginx configuration before reloading the production gateway. After deployment, verify both the live certificate and renewal path:

```bash
sudo certbot renew --dry-run
docker ps | grep gateway
```

## Install the renewal hook

Copy the repository hook into Certbot's deploy-hook directory:

```bash
sudo cp \
  /var/www/containers/scripts/ssl-renewal-hook.sh \
  /etc/letsencrypt/renewal-hooks/deploy/ssl-renewal-hook.sh
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/ssl-renewal-hook.sh
```

The deploy workflow keeps the source script under `/var/www/containers/scripts/` current, but the initial installation into Certbot's hook directory is a host setup step.

## IPv6

If a domain publishes an AAAA record, the gateway must listen on IPv6 for both HTTP and HTTPS. Let's Encrypt may validate through IPv6 when it is available in DNS.

## Troubleshooting

- Confirm `/var/www/letsencrypt` exists on the host and is mounted read-only into the gateway.
- Confirm the challenge location is evaluated before the HTTP-to-HTTPS redirect.
- Remove any redundant cron entry when renewal is already managed by `certbot.timer`.
- Do not add the HTTPS server block until Certbot has created the referenced files.
- Use `docker compose logs gateway` and `nginx -t` inside the gateway container when a reload fails.
