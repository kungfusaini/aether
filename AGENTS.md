# Aether Chest - Agent Guidelines

## Build & Development Commands

### Frontend (sumeetsaini_com)
- `npm run dev` - Start live-server on port 8080
- No linting/testing configured - use browser console for debugging

### Backend (vulkan) 
- `npm run dev` - Start nodemon for Express.js on port 3000
- `npm test` - No tests configured (placeholder)

### Full Stack
- `docker compose -f docker-compose.yml -f docker-compose-dev.yml up` - Start all services (dev)
- `docker compose -f docker-compose.yml -f docker-compose-prod.yml up` - Start all services (prod)

## Code Style Guidelines

### JavaScript/TypeScript
- **Frontend**: ES6 modules, import statements at top
- **Backend**: CommonJS require() statements
- Use camelCase for variables/functions
- Use UPPER_SNAKE_CASE for constants
- Event-driven architecture with custom emit/on functions
- Error handling with console.error() and early returns

### CSS
- CSS custom properties (variables) in :root
- BEM-style naming for classes
- Mobile-first responsive design
- Flexbox/Grid for layouts

### File Organization
- Modular structure: controller/, shape/, popup/, shared/
- Configuration in separate config.js files
- State management in dedicated state files
- Route handlers in routes/ directory (backend)

### Security
- Helmet middleware for security headers
- Rate limiting on contact endpoints
- Input validation and sanitization
- Environment-based configuration

## Deployment
- SSH to VPS: `ssh aether`