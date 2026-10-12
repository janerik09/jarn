import { useEffect, useState } from 'react'
import { formatDateTime } from '../lib/displayTime'

interface Backup {
  volid: string
  ctime: number | null
  size: number | null
}

interface Check {
  name: string
  ok: boolean
  detail: string
}

interface Preflight {
  ok: boolean
  volid: string
  target_storage: string | null
  disks: { key: string; storage: string; size_bytes: number; target_storage: string }[]
  checks: Check[]
  errors: string[]
  warnings: string[]
  candidates: { storage: string; type: string; available_bytes: number; total_bytes: number; fits: boolean }[]
}

const base = () => `/api/v1`

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes && bytes !== 0) return '–'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`
}

export function ServerBackups({ serverId }: { serverId: string }) {
  const [backups, setBackups] = useState<Backup[]>([])
  const [notMapped, setNotMapped] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [restoreVolid, setRestoreVolid] = useState<string | null>(null)
  const [preflight, setPreflight] = useState<Preflight | null>(null)
  const [preflightError, setPreflightError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [targetStorage, setTargetStorage] = useState('')

  const load = async () => {
    try {
      const res = await fetch(`${base()}/servers/${serverId}/backups`)
      const data = await res.json()
      if (!res.ok) {
        setNotMapped(true)
        setBackups([])
        return
      }
      setNotMapped(false)
      setBackups(data.backups || [])
    } catch (err) {
      setMessage({ ok: false, text: 'Could not load backups' })
    }
  }

  useEffect(() => {
    load()
  }, [serverId])

  const waitForTask = async (taskId: string): Promise<{ ok: boolean; result?: any; error?: string }> => {
    for (;;) {
      await new Promise(resolve => setTimeout(resolve, 3000))
      const res = await fetch(`${base()}/backup/tasks/${taskId}`)
      const data = await res.json()
      if (data.state === 'SUCCESS') return { ok: true, result: data.result }
      if (data.state === 'FAILURE') return { ok: false, error: data.error }
    }
  }

  const checkRestore = async (volid: string, storage: string) => {
    setChecking(true)
    setPreflightError(null)
    try {
      const params = new URLSearchParams({ volid })
      if (storage) params.set('target_storage', storage)
      const res = await fetch(`${base()}/servers/${serverId}/restore-preflight?${params}`)
      const data = await res.json()
      if (!res.ok) {
        setPreflight(null)
        setPreflightError(data.detail || 'Could not check this restore')
        return
      }
      setPreflight(data)
    } catch (err) {
      setPreflightError('Could not check this restore')
    } finally {
      setChecking(false)
    }
  }

  const openRestore = (volid: string) => {
    setMessage(null)
    setRestoreVolid(volid)
    setTargetStorage('')
    setPreflight(null)
    checkRestore(volid, '')
  }

  const closeRestore = () => {
    setRestoreVolid(null)
    setPreflight(null)
    setPreflightError(null)
  }

  const chooseStorage = (storage: string) => {
    setTargetStorage(storage)
    if (restoreVolid) checkRestore(restoreVolid, storage)
  }

  const run = async (kind: 'backup' | 'restore', volid?: string, target?: string) => {
    setMessage(null)
    setBusy(kind)
    try {
      const res = await fetch(
        `${base()}/servers/${serverId}/${kind}`,
        kind === 'backup'
          ? { method: 'POST' }
          : {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ volid, confirm: true, target_storage: target || null }),
            }
      )
      const data = await res.json()
      if (!res.ok) {
        setMessage({ ok: false, text: data.detail || `${kind} failed` })
        return
      }
      setMessage({ ok: true, text: kind === 'backup' ? 'Backup running…' : 'Restore running…' })
      const outcome = await waitForTask(data.task_id)
      setMessage(outcome.ok
        ? { ok: true, text: kind === 'backup' ? `Backup created: ${outcome.result.volid}` : `Restored ${outcome.result.restored}` }
        : { ok: false, text: outcome.error || `${kind} failed` })
      await load()
    } catch (err) {
      setMessage({ ok: false, text: `${kind} failed` })
    } finally {
      setBusy(null)
    }
  }

  const confirmRestore = () => {
    if (!restoreVolid || !preflight?.ok) return
    const where = targetStorage ? `to storage ${targetStorage}` : 'to the original storage of each disk'
    if (!window.confirm(`Restore this VM ${where} from the selected backup? Anything changed since the backup will be lost.`)) return
    const volid = restoreVolid
    const target = targetStorage
    closeRestore()
    run('restore', volid, target)
  }

  return (
    <div className="detail-section">
      <h4>Backups</h4>
      {notMapped ? (
        <p className="settings-help">Map this server to its VM in Admin Settings → Backup first.</p>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <button onClick={() => run('backup')} disabled={busy !== null}>
              {busy === 'backup' ? 'Backing up…' : 'Back up now'}
            </button>
          </div>
          {backups.length === 0 ? (
            <p className="settings-help">No backups yet</p>
          ) : (
            <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
              {backups.slice(0, 5).map(b => (
                <li key={b.volid} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4, fontSize: '0.85em' }}>
                  <span>{b.ctime ? formatDateTime(new Date(b.ctime * 1000)) : b.volid}</span>
                  <button onClick={() => openRestore(b.volid)} disabled={busy !== null || restoreVolid !== null}>
                    {busy === 'restore' ? 'Restoring…' : 'Restore'}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {restoreVolid && (
        <div className="restore-panel" style={{ border: '1px solid var(--border-bright)', borderRadius: 6, padding: 12, marginTop: 10 }}>
          <h5 style={{ margin: '0 0 8px' }}>Restore check</h5>
          {checking && !preflight && <p className="settings-help">Checking the backup, storage and permissions…</p>}
          {preflightError && <p className="settings-help" style={{ color: '#c8514c' }}>{preflightError}</p>}
          {preflight && (
            <>
              <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 10px', fontSize: '0.85em' }}>
                {preflight.checks.map(c => (
                  <li key={c.name} style={{ color: c.ok ? '#3a9d6e' : '#c8514c', marginBottom: 2 }}>
                    {c.ok ? '✓' : '✗'} {c.detail}
                  </li>
                ))}
                {preflight.warnings.map(w => (
                  <li key={w} style={{ color: '#d8a33a', marginBottom: 2 }}>! {w}</li>
                ))}
              </ul>

              <table style={{ width: '100%', fontSize: '0.85em', marginBottom: 10 }}>
                <thead>
                  <tr><th style={{ textAlign: 'left' }}>Disk</th><th style={{ textAlign: 'left' }}>Size</th><th style={{ textAlign: 'left' }}>Restores to</th></tr>
                </thead>
                <tbody>
                  {preflight.disks.map(d => (
                    <tr key={d.key}>
                      <td>{d.key}</td>
                      <td>{formatBytes(d.size_bytes)}</td>
                      <td>{d.target_storage}{!targetStorage && <span className="settings-help"> (original)</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <label style={{ display: 'block', fontSize: '0.9em', marginBottom: 8 }}>
                Restore storage{' '}
                <select value={targetStorage} onChange={e => chooseStorage(e.target.value)} disabled={checking}>
                  <option value="">Original storage of each disk</option>
                  {preflight.candidates.map(c => (
                    <option key={c.storage} value={c.storage}>
                      {c.storage} ({c.type}) — {formatBytes(c.available_bytes)} free{c.fits ? '' : ', too small for whole VM'}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={confirmRestore} disabled={!preflight?.ok || checking || busy !== null}>
              Restore now
            </button>
            <button onClick={closeRestore}>Cancel</button>
          </div>
        </div>
      )}

      {message && (
        <p className="settings-help" style={{ color: message.ok ? '#3a9d6e' : '#c8514c' }}>{message.text}</p>
      )}
    </div>
  )
}
