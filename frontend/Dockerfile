# ── DataWhisper frontend (build → static nginx) ───────────────────────────────
FROM node:26-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# API base is same-origin (/api) by default and proxied by nginx, so no build arg
# is required. Override VITE_API_URL only for split-origin deployments.
RUN npm run build

FROM nginx:1.31-alpine
# Pull current Alpine security patches (musl, zlib, libxml2, nghttp2 ...) rather
# than inheriting whatever the base tag was built with. The image is Trivy-scanned
# on every CI run, which is what keeps this honest.
RUN apk upgrade --no-cache
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s CMD wget -q -O /dev/null http://localhost/ || exit 1
