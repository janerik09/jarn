# Recovery

This runbook covers restoring JÁRN after the server is lost or its database is damaged. Read the section on keys first:
without `CREDENTIAL_ENCRYPTION_KEY`, the stored SSH keys, passwords and backup tokens cannot be decrypted, even with
a perfect database copy.

## Objectives

| | Target | What decides it |
|---|---|---|
| Recovery point (data you can lose) | 24 hours | Daily database dump, and the daily off-host copy when it is on |
| Recovery time | About 1 hour once the host and this runbook are available | Database restore plus a health check |

These are targets for an installation that follows this runbook. Measure your own time with the restore drill below.

## What must be kept, and where

| Item | Where it lives | Why |
|---|---|---|
| `.env` (all three secrets) | Offline copy in a password manager or sealed envelope. Never in the repository. | Without the key, encrypted credentials are lost. Without `SECRET_KEY`, the old sessions stop working (acceptable). |
| Database dumps (`backups/patching_db-*.dump`) | On this host, and off-host through **Admin Settings, Off-host copy** | The platform's data: servers, jobs, audit trail, settings |
| Audit archives (`backups/audit/*.jsonl.gz`) | Same as the dumps | Events older than the retention period |
| Proxmox VM backups | Proxmox Backup Server, managed in Proxmox | Not part of JÁRN's copy. Restore VMs from Proxmox. |
| `tls/` | Optional | Certificates can be regenerated or re-uploaded in Admin Settings, Web access |

Check the off-host copy is running on the dashboard. A banner means backups are not protected off-host.

## Restore to a new host

1. Install Docker and the Compose plugin. Clone the repository (or copy the release).
2. Restore `.env` from your offline copy into the project root. Check the file mode is `600`.
3. Copy the database dump you want into `backups/`. Pick the newest good one, from this host or from the off-host
   folder. Dumps are named `patching_db-YYYYMMDD-HHMMSS.dump`, so the newest is the highest name.
4. Start the database alone:
   ```bash
   docker compose up -d postgres
   ```
5. Restore the dump into a new database:
   ```bash
   docker compose exec -T postgres sh -c 'createdb -U patching_user patching_db' 2>/dev/null || true
   docker compose exec -T postgres pg_restore -U patching_user -d patching_db --clean --if-exists < backups/patching_db-YYYYMMDD-HHMMSS.dump
   ```
   Use the file name you chose in step 3.
6. Start everything:
   ```bash
   docker compose up -d --build
   curl http://localhost/health
   ```
   Expect `"status": "ok"`. The backend applies any migrations newer than the dump.
7. Check the audit trail is intact: sign in as an administrator, open **Audit Logs**, and press **Check integrity**.
8. Check credentials decrypt: open a server and press **Test connection**. A "could not decrypt" error means
   `CREDENTIAL_ENCRYPTION_KEY` in `.env` is not the key used when the data was written.
9. Re-approve SSH host keys. Host keys are stored with each server, so they survive the restore, but confirm them
   against each server if the server itself was rebuilt: **Admin Settings, Server host keys**.

## If the key is lost

Stored SSH keys, passwords and backup tokens cannot be recovered. Everything else restores. After restoring without
the key:

1. Generate a new `CREDENTIAL_ENCRYPTION_KEY` (see `.env.example`) and put it in `.env`.
2. Re-enter the SSH key or password for each server, and the Proxmox and off-host credentials, in the screens for them.
3. Approve each server's host key again.

Record the new key in your offline copy at the same time. Treat losing the key as an incident.

## Changing keys on a healthy system

- **Credential encryption key:** not supported yet. Changing it makes stored credentials unreadable, so plan a
  re-entry of every credential, as above, before changing it.
- **Signing key (`SECRET_KEY`):** change it in `.env` and restart the backend. Everyone is signed out.

## Restore drill

Run this at least every quarter, on a spare host or a scratch database, and write down how long it took:

1. Restore the newest off-host dump, as above, into a scratch database name.
2. Confirm the table count matches the production database.
3. Run the integrity check in **Audit Logs**.
4. Record the date, the dump used and the time taken.

The database backup container already restores its newest dump into a scratch database once a week, and the platform
alerts if that has not passed for 14 days. The drill covers the parts that container cannot test: the `.env` keys and
the off-host copy.

## Known gaps

- The database and worker run on one host. A host loss means a restore, not a failover.
- Proxmox backups are outside this runbook. Their restore is in Proxmox's documentation.
