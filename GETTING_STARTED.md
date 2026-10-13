# Getting started

This guide takes you from a fresh copy of the code to a working installation: the secrets, the first start, the
first administrator, and the first server.

## What you need

- Docker, with the Compose plugin. Check with `docker compose version`.
- A host that can reach your servers over SSH. If you use Proxmox backups, it also needs to reach the Proxmox API.
- Ports 80 free on the host, and 443 too if you want HTTPS. See [NETWORK_ACCESS.md](NETWORK_ACCESS.md).

## 1. Create the secrets

```bash
cp .env.example .env
```

Fill in the three required values. `.env.example` and the README show how to generate each one.

- `POSTGRES_PASSWORD`: any strong value.
- `SECRET_KEY`: at least 32 random characters.
- `CREDENTIAL_ENCRYPTION_KEY`: 32 random bytes, base64 encoded.

Keep a copy of `.env` somewhere safe. The encryption key can't be recovered. Without it, the SSH keys and passwords
JÁRN has stored are lost.

## 2. Start the stack

```bash
docker compose up -d --build
docker compose ps
```

Wait until `patching-postgres` reports healthy and `patching-backend` is running. The backend applies the database
migrations when it starts. Then check the health endpoint:

```bash
curl http://localhost/health
```

You should see `"status": "ok"`, with the database, Redis and the worker all reporting `ok`. The worker can take a
few seconds to come up.

## 3. Create the administrator

Open `http://<host>/` in a browser. On the first visit you'll see a form to create the first administrator account.
Enter a username, an email address if you like, and a password. The form only appears while there are no users. After
that, you add other accounts from **Admin Settings → Users**.

Your session lasts 15 minutes at a time, and the browser renews it automatically. Signing out ends it.

### Optional: authenticator app (MFA)

Click your username in the sidebar, then **Account**. Press **Set up MFA**, add the key to Google Authenticator,
Microsoft Authenticator or another TOTP app, enter the first code, and press **Turn on MFA**. You'll get ten recovery
codes. Each one works once, and they're only shown once, so save them somewhere safe.

### Forgotten password

From the server:

```bash
docker compose exec backend python -m app.cli reset-password <username>
```

The command asks for the new password and never prints it.

## 4. Connect Proxmox (optional)

Go to **Admin Settings → Backup**. Add a target with its API URL (usually `https://<proxmox-host>:8006`) and an API
token, then save. Enter the token secret only there. After that, map each server to its VM or container. Backups only
run when you start them.

## 5. Turn on the off-host copy

This is strongly recommended. Go to **Admin Settings → Off-host copy**, enter an SFTP destination, and press
**Test connection**. Compare the fingerprint it shows with the one your NAS shows, approve it, and save. The README has
the details.

Until a copy is running, the dashboard shows a warning and an alert stays open. If you can't set up a destination yet,
record a waiver with a reason on the same screen. Everyone who signs in can see the waiver.

## 6. Add a server

1. Go to **Servers → + Add Server**. Enter the hostname or IP address, the SSH port, a username, and either a private
   key or a password. Credentials are encrypted before they're stored.
2. **Approve the server's SSH host key.** JÁRN won't connect to a server until its key is approved. Go to
   **Admin Settings → Server host keys** and press **Read keys from all servers**. Compare each fingerprint with the
   one on the server itself, which you can get with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. Approve only
   the ones that match. Existing servers need this once. New servers need it before their first connection.
3. Open the server. Its package scan and health details appear after the first check runs.
4. Patch it with **Create Job**, or through a patch group or a schedule.

Start with a test machine, and make the first job a single server with the approval and rollback settings left on.

## Turning things off

- **MFA:** **Account → Turn off MFA**. This needs your password.
- **Off-host copy:** untick the enable box and save. Copies already on the remote server stay there.
- **A user:** set the account to inactive in **Admin Settings → Users**. Their sessions end at their next refresh.

## When something looks wrong

| Symptom | What to check |
|---|---|
| The sign-in page says "The server is restarting" | Run `docker compose logs backend`. Migrations are usually still running. |
| Sign-in says "Too many sign-in attempts" | Wait 15 minutes. The limit applies per address and per account. |
| Health shows `worker: no worker responding` | Run `docker compose logs worker`, and check that Redis is healthy. |
| The off-host test says the host key changed | The server presents a different key from the one you approved. Find out why before approving the new key. |
| A restore is blocked | Read the checks in the restore panel. Each failure says what to fix. Nothing was stopped. |
| A job says `completed` but some servers failed | That's how it works: a job finishes whatever the servers' results. Open the job and check each server. The failure count is in the job summary. |
| A job shows `failed` after many hours | The watchdog stopped it, because the worker died mid-run. Check the servers that were pending or running before you retry them. |
