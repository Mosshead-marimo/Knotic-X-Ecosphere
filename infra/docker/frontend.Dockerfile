# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM node:24.19.0-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS builder
WORKDIR /workspace
RUN npm install --global npm@11.17.0
COPY package.json package-lock.json .npmrc ./
COPY frontend/package.json frontend/package.json
RUN npm ci
COPY frontend frontend
RUN npm run lint --workspace @knotic/frontend
RUN npm run typecheck --workspace @knotic/frontend
RUN npm run build --workspace @knotic/frontend

FROM node:24.19.0-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN groupadd --gid 10001 knotic && useradd --uid 10001 --gid knotic --no-create-home --shell /usr/sbin/nologin knotic
WORKDIR /app
COPY --from=builder --chown=10001:10001 /workspace/frontend/.next/standalone ./
COPY --from=builder --chown=10001:10001 /workspace/frontend/.next/static ./frontend/.next/static
COPY --from=builder --chown=10001:10001 /workspace/frontend/public ./frontend/public
USER 10001:10001
EXPOSE 3000
CMD ["node", "frontend/server.js"]
