FROM nginx:stable-alpine

# Copy main nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Create directory for site configurations
RUN mkdir -p /etc/nginx/conf.d

# Default configuration will be mounted via volume
CMD ["nginx", "-g", "daemon off;"]