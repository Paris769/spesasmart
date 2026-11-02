FROM node:22-alpine AS build
WORKDIR /build
COPY package.json tsconfig.json ./
RUN npm install --no-audit --no-fund
COPY webhook-proxy.ts ./
RUN npm run build

FROM node:22-alpine
RUN apk add --no-cache tini \
    && addgroup -S proxy -g 10002 \
    && adduser -S proxy -G proxy -u 10002 -h /home/proxy -s /sbin/nologin
WORKDIR /app
COPY --from=build /build/dist ./dist
COPY --from=build /build/node_modules ./node_modules
COPY --from=build /build/package.json ./
USER proxy
EXPOSE 8443
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "--enable-source-maps", "dist/webhook-proxy.js"]
