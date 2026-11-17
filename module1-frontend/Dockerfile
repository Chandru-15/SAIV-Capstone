FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm ci

# Copy application code
COPY . .

# Build the application
# /api/config reads public URLs from the running container, not the build bundle.
# Next.js compiles rewrites at build time; the Docker proxy needs service DNS.
ARG SAIV_BACKEND_INTERNAL_URL=http://backend:8000
RUN npm run build

# Expose port
EXPOSE 3000

# Run the application
CMD ["npm", "start"]
