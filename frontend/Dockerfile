# ── DataWhisper frontend (build → static nginx) ───────────────────────────────
# Node 20 reached end of life on 2026-04-30 and no longer receives security
# fixes. 24 is the Active LTS (maintenance from 2026-10-20, EOL 2028-04-30) —
# not 26, which is still Current and does not become LTS until 2026-10-28.
# Only the build stage runs Node; the shipped image is the nginx stage below.
FROM node:24-alpine AS build
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
