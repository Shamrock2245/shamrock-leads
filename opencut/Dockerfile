# OpenCut web (edit.shamrockbailbonds.biz) — production standalone on the VPS.
# Clones the public classic tree so /opt/shamrock-leads can build without a
# sibling checkout. Pin OPENCUT_REF to a commit SHA for reproducible deploys.
#
# Build: docker compose --profile edit build opencut
# Run:   docker compose --profile edit up -d

FROM oven/bun:alpine AS builder

WORKDIR /app

RUN apk add --no-cache git python3 make g++

ARG OPENCUT_REPO=https://github.com/OpenCut-app/opencut-classic.git
ARG OPENCUT_REF=main
ARG NEXT_PUBLIC_SITE_URL=https://edit.shamrockbailbonds.biz
ARG NEXT_PUBLIC_MARBLE_API_URL=https://api.marblecms.com
ARG MARBLE_WORKSPACE_KEY=build-placeholder
ARG FREESOUND_CLIENT_ID
ARG FREESOUND_API_KEY

RUN git clone --depth 1 --branch "${OPENCUT_REF}" "${OPENCUT_REPO}" .

RUN bun install

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV DATABASE_URL="postgresql://opencut:opencut@localhost:5432/opencut"
ENV BETTER_AUTH_SECRET="build-time-secret"
ENV UPSTASH_REDIS_REST_URL="http://localhost:8079"
ENV UPSTASH_REDIS_REST_TOKEN="example_token"
ENV NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL
ENV NEXT_PUBLIC_MARBLE_API_URL=$NEXT_PUBLIC_MARBLE_API_URL
ENV MARBLE_WORKSPACE_KEY=$MARBLE_WORKSPACE_KEY
ENV FREESOUND_CLIENT_ID=$FREESOUND_CLIENT_ID
ENV FREESOUND_API_KEY=$FREESOUND_API_KEY

WORKDIR /app/apps/web
RUN bun run build

FROM oven/bun:alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/apps/web/public ./apps/web/public
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/static ./apps/web/.next/static

RUN chown nextjs:nodejs apps

USER nextjs
EXPOSE 3000

CMD ["bun", "apps/web/server.js"]
