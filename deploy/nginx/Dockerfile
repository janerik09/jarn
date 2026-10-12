# Builds the dashboard and serves it with nginx. Also terminates HTTPS when enabled in Admin Settings.
FROM node:26-alpine@sha256:143494b1da2945f061539253adc65e4f1569ddf07da2d384c022c791a9d90a4a AS build
WORKDIR /app
COPY frontend-web/package.json frontend-web/package-lock.json* ./
RUN npm ci
COPY frontend-web/ ./
RUN npm run build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
RUN apk upgrade --no-cache && rm -f /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx/templates/ /etc/nginx/templates/
COPY deploy/nginx/websocket.conf /etc/nginx/conf.d/00-websocket.conf
COPY deploy/nginx/entrypoint.sh /entrypoint-tls.sh
RUN chmod +x /entrypoint-tls.sh
ENTRYPOINT ["/entrypoint-tls.sh"]
