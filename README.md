# Aether Chest

A containerized personal website with interactive 3D graphics and backend API services.

## Architecture

**Multi-service Docker application:**
- **sumeetsaini_com**: Frontend with Three.js 3D animations and interactive shapes
- **vulkan**: Express.js API backend with contact form handling
- **gateway**: Nginx reverse proxy for routing and load balancing

## Tech Stack

### Frontend
- Vanilla JavaScript with ES6 modules
- Three.js for 3D graphics and animations
- CSS Grid/Flexbox for responsive layout
- Live-server for development

### Backend
- Node.js with Express 5.x
- Helmet for security headers
- Express-rate-limit for API protection
- Nodemailer for contact form emails

### Infrastructure
- Docker & Docker Compose
- Nginx reverse proxy
- Environment-based configuration

## Services

| Service | Port | Purpose |
|---------|------|---------|
| sumeetsaini_com | 8080 | Static frontend with 3D interactions |
| vulkan | 3000 | API endpoints (status, contact) |
| gateway | 80/443 | Nginx reverse proxy |

## Development

```bash
# Start all services
docker-compose -f docker-compose-dev.yml up

# Frontend only
cd sumeetsaini_com && npm run dev

# Backend only  
cd vulkan && npm run dev
```

## API Endpoints

- `GET /status` - Health check
- `POST /web_contact` - Contact form (rate limited: 5/15min)

## Features

- Interactive 3D shape animations
- Responsive design
- Rate-limited contact API
- Security-hardened backend
- Containerized deployment