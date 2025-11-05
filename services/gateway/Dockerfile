FROM nginx:stable-alpine

# Copy main nginx configuration (will be overridden by volume mount)
COPY nginx.conf /etc/nginx/nginx.conf

# Copy all configuration files
COPY conf.d/ /etc/nginx/conf.d/

# Configuration will be mounted via volume
CMD ["nginx", "-g", "daemon off;"]