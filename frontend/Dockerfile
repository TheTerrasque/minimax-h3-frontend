FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Unprivileged variant (runs as a non-root user, listens on unprivileged
# ports) instead of the plain nginx image -- so this image satisfies
# Kubernetes clusters that enforce the "restricted" Pod Security Standard
# (runAsNonRoot), with no securityContext workarounds needed.
FROM nginxinc/nginx-unprivileged:1.27-alpine
# Processed by the base image's docker-entrypoint.d via envsubst at
# container start -- see nginx.conf.template's own header comment.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
# NGINX_ENTRYPOINT_LOCAL_RESOLVERS opts into the base image's own
# 15-local-resolvers.envsh, which auto-detects nameservers from
# /etc/resolv.conf into $NGINX_LOCAL_RESOLVERS (used by the template as
# ${NGINX_LOCAL_RESOLVERS}) -- Docker's embedded DNS under docker-compose,
# your cluster's CoreDNS/kube-dns under Kubernetes, no manual configuration
# needed either way. BACKEND_SERVICE_HOST/PORT reproduce today's
# docker-compose.yml service name/port exactly; override them (as
# container env vars, no rebuild needed) wherever the backend Service is
# named/ported differently, e.g. a Kubernetes Deployment in another
# namespace.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    BACKEND_SERVICE_HOST=backend \
    BACKEND_SERVICE_PORT=8000
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
