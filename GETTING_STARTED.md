# Getting started

This takes you from a fresh checkout to a working installation: secrets, first start, the first administrator,
and the first server.

## Requirements

- Docker with the Compose plugin (`docker compose version`).
- A host that can reach your servers over SSH, and the Proxmox VE API if you use Proxmox backups.
- Ports 80 (and 443 if you enable HTTPS) free on the host. See [NETWORK_ACCESS.md](NETWORK_ACCESS.md).

## 1. Create the secrets

```bash
cp .env.example .env
```

Fill in the three required values. The commands that generate them are in `.env.example` and in the README.

- `POSTGRES_PASSWORD`: any strong value.
- `SECRET_KEY`: at least 32 random characters.
- `CREDENTIAL_ENCRYPTION_KEY`: 32 random bytes, base64 encoded.

Keep a copy of `.env` somewhere safe. The encryption key cannot be recovered; without it, stored SSH keys and
passwords are lost.

## 2. Start the stack

```bash
docker compose up -d --build
docker compose ps
```

Wait until `patching-postgres` reports healthy and `patching-backend` is running. The backend applies database
migrations when it starts. Check the health endpoint:

```bash
curl http://localhost/health
```

It should report `"status": "ok"`, with database, redis and worker all `ok`. The worker can take a few seconds
after start.

## 3. Create the administrator

Open `http://<host>/` in a browser. The first visit shows the setup form: "Create the first administrator account". Enter a username,
an optional email address and a password. This form only appears while there are no users. Setup is then closed,
and later accounts are created from **Admin Settings → Users**.

You stay signed in for 15 minutes at a time and the browser renews the session automatically. Signing out ends it.

### Optional: authenticator-app MFA

Open your username in the sidebar, then **Account**. Press **Set up MFA**, add the key to Google Authenticator,
Microsoft Authenticator or another TOTP app, and enter the first code, then press **Turn on MFA**. Save the ten recovery codes
somewhere safe; each one works once and they are shown only once.

### Forgotten password

On the server:

```bash
docker compose exec backend python -m app.cli reset-password <username>
```

It prompts for the new password and never prints it.

## 4. Connect Proxmox (optional)

Go to **Admin Settings → Backup**, add a target with its API URL and an API token, and save. Use
`https://<proxmox-host>:8006`. Enter the token secret only here. Then map each server to its VM or container.
Backups only run when you start them.

## 5. Turn on the off-host copy (strongly recommended)

Go to **Admin Settings → Off-host copy**. Enter an SFTP destination, press **Test connection**, check the
fingerprint against the one your NAS shows, approve it, then save. Details are in the README.

Until a copy is running, the dashboard shows a warning and an alert stays open. If you cannot provide a destination,
record a waiver with a reason in the same screen. The waiver is shown to everyone who signs in.

## 6. Add a server

1. **Servers → + Add Server**. Enter the hostname or IP, the SSH port, a username, and either a private key or a
   password. Credentials are encrypted before they are stored.
1a. **Approve the server's SSH host key.** JÁRN refuses to connect to a server until you approve its key. Go to
   **Admin Settings → Server host keys**, press **Read keys from all servers**, and compare each fingerprint with the
   one the server shows (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server). Approve only matching keys.
   Existing servers need this once; new servers need it before their first connection.
2. Open the server. Its package scan and health details appear after the first check runs.
3. Patch it with **Create Job**, or use a patch group or a schedule.

Start with a test machine. The first patch job should be a single server, with the approval and rollback settings
left on.

## Turning things off

- MFA: **Account → Turn off MFA** (needs your password).
- Off-host copy: untick the enable box and save. Existing copies stay on the remote server.
- Users: set an account to inactive in **Admin Settings → Users**. Its sessions end at the next refresh.

## Troubleshooting

| Symptom | Check |
|---|---|
| Sign-in page shows "The server is restarting" | `docker compose logs backend`. Usually migrations are still running. |
| Sign-in says "Too many sign-in attempts" | Wait 15 minutes. The limit applies per address and per account. |
| Health reports `worker: no worker responding` | `docker compose logs worker`. Check Redis is healthy. |
| Off-host test says the host key changed | The server's key differs from the approved one. Confirm the change on the server before approving the new key. |
| Restore is blocked | Read the checks in the restore panel; each failure says what to fix. Nothing was stopped. |
| A job shows `completed` but some servers failed | This is expected: the job finishes whatever the server results. Open the job and check each server; the failed count is in the job summary. |
| A job stopped with `failed` after many hours | The watchdog stopped it because the worker stopped mid-run. Check the servers that were pending or executing before retrying them. |
