# Network access

## What is published

In the production layout (`docker compose up -d`), only the web container publishes ports:

| Port | Container | Purpose |
|---|---|---|
| 80 | `web` (nginx) | Dashboard, `/api`, and `/health`. HTTP by default. |
| 443 | `web` (nginx) | Dashboard over HTTPS, when enabled in Admin Settings → Web access. |

The API (8000), PostgreSQL (5432), Redis (6379) and `/metrics` are on the internal Docker network only. Prometheus
scrapes the backend directly. Nothing else needs to be opened on the host.

The development overlay (`docker-compose.dev.yml`) also publishes 8000, 5173, 5432 and 6379. Do not run the overlay
on a network other people can reach.

## Open the dashboard

From the host: `http://localhost/`. From another machine on the same network: `http://<host-ip>/`.

The sign-in page works over plain HTTP. Sessions still stay signed in through the browser's refresh cookie, which
is marked Secure only when the page is served over HTTPS.

## HTTPS

Go to **Admin Settings → Web access**.

- **HTTP** (default): the dashboard is served on port 80 only.
- **HTTPS with a generated certificate**: creates a self-signed certificate. Browsers warn until you trust it.
  The certificate can be downloaded from the same page.
- **HTTPS with your own certificate**: upload the certificate and private key files. The key is stored encrypted.
- **Redirect HTTP to HTTPS** is optional and applies once HTTPS is active.

Changes are applied by reloading nginx automatically once the configuration validates. The page shows whether the
applied configuration is current.

## Firewalls

Allow only what users need. For a LAN-only install on 192.168.1.0/24:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 80 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 443 proto tcp
```

Do not open 8000, 5432, 6379 or 9090 to the network.

## Reaching the database or API from your workstation

Use an SSH tunnel rather than publishing ports:

```bash
ssh -L 8000:localhost:8000 user@jarn-host
```

That only works with the development overlay, which publishes 8000 on the host.

## Browser origins and CORS

The dashboard calls `/api/v1` on the same origin, so no CORS setting is needed. `CORS_ORIGINS` exists for a
separately hosted front end; list only the exact origins that need it. Cross-site requests cannot send the refresh
cookie.

## Metrics

`/metrics` is not served through the web container. To scrape it from outside the Docker network, set
`METRICS_TOKEN` in `.env` and send `Authorization: Bearer <token>`. The bundled Prometheus config uses the internal
network and is described in the file's comments.
