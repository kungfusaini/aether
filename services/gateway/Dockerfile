FROM nginx:stable-alpine

# Copy production nginx configuration
COPY nginx-prod.conf /etc/nginx/nginx.conf

# Copy all configuration files
COPY conf.d/ /etc/nginx/conf.d/

# Copy custom error pages
COPY html/ /usr/share/nginx/html/

CMD ["nginx", "-g", "daemon off;"]