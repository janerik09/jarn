# Network access

## What is exposed

In the production setup (`docker compose up -d`), only the web container publishes ports:

| Port | Container | What it serves |
|---|---|---|
| 80 | `web` (nginx) | The dashboard, `/api` and `/health`. HTTP by default. |
| 443 | `web` (nginx) | The same, over HTTPS, once you turn it on in Admin Settings → Web access. |

The API (8000), PostgreSQL (5432), Redis (6379) and `/metrics` are only reachable on the internal Docker network.
Prometheus connects to the backend directly. You don't need to open anything else on the host.

The development overlay (`docker-compose.dev.yml`) also publishes 8000, 5173, 5432 and 6379. Don't run the overlay on
a network that other people can reach.

## Opening the dashboard

From the host itself, use `http://localhost/`. From another machine on the same network, use `http://<host-ip>/`.

The sign-in page works over plain HTTP. The browser's refresh cookie is marked Secure only when the page is served
over HTTPS, so over HTTP the cookie is sent without that flag. Use HTTPS for anything beyond a test network.

## HTTPS

Go to **Admin Settings → Web access**. You have three choices:

- **HTTP** is the default. The dashboard is served on port 80 only.
- **HTTPS with a generated certificate** creates a self-signed certificate. Browsers will warn until you trust it.
  You can download the certificate from the same page.
- **HTTPS with your own certificate.** Upload the certificate and the private key. The key is stored encrypted.

You can also turn on a redirect from HTTP to HTTPS, which applies once HTTPS is active.

When you save, nginx reloads automatically, as long as the new configuration validates. The page shows whether the
running configuration matches what you saved.

## Firewall

Only open what people need. For a LAN-only install on 192.168.1.0/24, that's:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 80 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 443 proto tcp
```

Don't open 8000, 5432, 6379 or 9090 to the network.

## Reaching the API or database from your workstation

Use an SSH tunnel instead of publishing ports:

```bash
ssh -L 8000:localhost:8000 user@jarn-host
```

This only works with the development overlay, because that's the only setup that publishes port 8000 on the host.

## Cross-origin access and CORS

The dashboard calls `/api/v1` on its own address, so it doesn't need CORS. `CORS_ORIGINS` is only for a front end
hosted somewhere else. If you use it, list the exact origins that need access. Cross-site requests can't send the
refresh cookie anyway.

## Metrics

nginx doesn't forward `/metrics`, so there's no route to it from outside the Docker network. Prometheus scrapes the
backend directly over that internal network. If you set `METRICS_TOKEN` in `.env`, the endpoint also requires
`Authorization: Bearer <token>`. The Prometheus config in `deploy/monitoring` explains how to add the token.
