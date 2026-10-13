# Recovery

This guide explains how to restore JÁRN after you lose the server or its database. Read the section on keys first.
Without `CREDENTIAL_ENCRYPTION_KEY`, the stored SSH keys, passwords and backup tokens can't be decrypted, even if you
have a perfect copy of the database.

## Targets

| | Target | What it depends on |
|---|---|---|
| How much data you can lose | 24 hours | The daily database dump, and the daily off-host copy when it's on |
| How long a restore takes | About an hour, once you have the host and this guide | Restoring the database, then a health check |

These are targets, not promises. Time your own restore with the drill described below.

## What to keep, and where

| Item | Where it lives | Why |
|---|---|---|
| `.env`, with all three secrets | An offline copy in a password manager or a sealed envelope. Never in the repository. | Without the key, encrypted credentials are gone. Without `SECRET_KEY`, existing sessions stop working, which is acceptable. |
| Database dumps, `backups/patching_db-*.dump` | On this host, and off the host through **Admin Settings → Off-host copy** | The platform's data: servers, jobs, the audit trail and settings |
| Audit archives, `backups/audit/*.jsonl.gz` | The same places as the dumps | Events older than the retention period |
| Proxmox VM backups | Proxmox Backup Server, managed in Proxmox | These aren't part of JÁRN's copy. Restore VMs from Proxmox. |
| `tls/` | Optional | You can regenerate the certificates or upload them again under Admin Settings → Web access |

The dashboard shows a banner when backups aren't protected off the host. If you see it, fix that before anything else.

## Restoring to a new host

1. Install Docker and the Compose plugin. Clone the repository, or copy the release.
2. Copy `.env` from your offline copy into the project root, and make sure its permissions are `600`.
3. Copy the database dump you want into `backups/`. Use the newest good one, from this host or from the off-host
   folder. The file names include the timestamp, so the newest is the one with the highest name.
4. Start only the database:
   ```bash
   docker compose up -d postgres
   ```
5. Restore the dump into a fresh database:
   ```bash
   docker compose exec -T postgres sh -c 'createdb -U patching_user patching_db' 2>/dev/null || true
   docker compose exec -T postgres pg_restore -U patching_user -d patching_db --clean --if-exists < backups/patching_db-YYYYMMDD-HHMMSS.dump
   ```
   Replace the file name with the one you picked in step 3.
6. Start everything, then check the health endpoint:
   ```bash
   docker compose up -d --build
   curl http://localhost/health
   ```
   You should see `"status": "ok"`. The backend applies any migrations that are newer than the dump.
7. Check the audit trail. Sign in as an administrator, open **Audit Logs**, and press **Check integrity**.
8. Check that the credentials decrypt. Open a server and press **Test connection**. A "could not decrypt" error means
   the `CREDENTIAL_ENCRYPTION_KEY` in `.env` isn't the key that was used when the data was written.
9. Check the SSH host keys. They're stored with each server, so they survive a restore. If you rebuilt a server
   itself, confirm its key again under **Admin Settings → Server host keys**.

## If the encryption key is lost

Stored SSH keys, passwords and backup tokens can't be recovered. Everything else restores normally. Then:

1. Generate a new `CREDENTIAL_ENCRYPTION_KEY` (see `.env.example`), and put it in `.env`.
2. Enter the SSH key or password for each server again. Do the same for the Proxmox and off-host credentials.
3. Approve each server's host key again.

Save the new key to your offline copy straight away. Treat losing the key as an incident.

## Changing keys on a working system

- **The encryption key** can't be rotated yet. Changing it makes every stored credential unreadable, so re-enter them
  all as described above before you change the key.
- **`SECRET_KEY`** can be changed. Put the new value in `.env` and restart the backend. Everyone is signed out.

## The restore drill

Do this at least once a quarter, on a spare host or in a scratch database. Write down how long it took.

1. Restore the newest off-host dump into a scratch database, as described above.
2. Check that the table count matches the production database.
3. Run the integrity check in **Audit Logs**.
4. Record the date, the dump you used, and the time it took.

The database backup container already restores its newest dump into a scratch database every week. If that hasn't
happened for 14 days, the platform raises an alert. The drill covers what that check can't: the `.env` keys and the
off-host copy.

## Known gaps

- The database and the worker run on one host. If the host is lost, you restore; nothing fails over automatically.
- Proxmox backups are outside this guide. Their restore steps are in Proxmox's documentation.
