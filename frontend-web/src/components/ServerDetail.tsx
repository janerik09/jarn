import { useEffect, useState, KeyboardEvent } from 'react'
import './ServerDetail.css'
import { formatDateTime } from '../lib/displayTime'

interface AppCheck {
  command: string
  ok: boolean
  detail: string
}

interface Inventory {
  collected_at?: string
  error?: string
  os?: { id?: string; version?: string; pretty_name?: string }
  kernel?: string
  uptime_seconds?: number | null
  kubernetes_node?: boolean
  applications?: { id: string; name: string; containerized: boolean; healthy: boolean | null; checks: AppCheck[] }[]
  services?: string[]
  containers?: { name: string; image: string }[]
  ports?: string[]
}

interface Detail {
  id: string
  hostname: string
  ip_address: string | null
  os_type: string
  os_version: string | null
  auth_method: string
  port: string | null
  connectivity_status: string
  last_check_at: string | null
  last_patch_scan_at: string | null
  tags: string[]
  notes: string
  inventory: Inventory | null
  health: any
  pending_patch_count: number | null
  scan_error: string | null
  backup_mapping: any
  last_backup: { volid: string; created_at: string } | null
  recent_executions: { job_name: string; status: string; started: string | null; ended: string | null; error: string | null }[]
}

type Tab = 'overview' | 'applications' | 'services' | 'containers' | 'patching'

const base = () => `/api/v1`

export function ServerDetail({ serverId, onBack, onCreateJob }: { serverId: string; onBack: () => void; onCreateJob?: (serverId: string) => void }) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [tab, setTab] = useState<Tab>('overview')
  const [error, setError] = useState<string | null>(null)
  const [tags, setTags] = useState<string[]>([])
  const [tagInput, setTagInput] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const load = async () => {
    try {
      const res = await fetch(`${base()}/servers/${serverId}/detail`)
      if (!res.ok) throw new Error((await res.json()).detail || 'Could not load server')
      const data: Detail = await res.json()
      setDetail(data)
      setTags(data.tags || [])
      setNotes(data.notes || '')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load server')
    }
  }

  useEffect(() => {
    load()
  }, [serverId])

  const addTag = () => {
    const tag = tagInput.trim().toLowerCase()
    if (tag && !tags.includes(tag)) setTags([...tags, tag])
    setTagInput('')
  }

  const onTagKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addTag()
    }
  }

  const save = async () => {
    setSaving(true)
    setMessage(null)
    try {
      const res = await fetch(`${base()}/servers/${serverId}/tags-notes`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tags, notes }),
      })
      const data = await res.json()
      if (!res.ok) {
        setMessage(data.detail || 'Save failed')
        return
      }
      setMessage('Saved')
      load()
    } finally {
      setSaving(false)
    }
  }

  const refreshInventory = async () => {
    setRefreshing(true)
    setMessage(null)
    try {
      const res = await fetch(`${base()}/servers/${serverId}/inventory`, {
        method: 'POST',
        signal: AbortSignal.timeout(10000),
      })
      const data = await res.json()
      if (!res.ok) {
        setMessage(data.detail || 'Could not start the inventory')
        return
      }
      const deadline = Date.now() + 3 * 60 * 1000
      let outcome: 'SUCCESS' | 'FAILURE' | 'TIMEOUT' = 'TIMEOUT'
      while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 2000))
        try {
          const status = await (await fetch(`${base()}/backup/tasks/${data.task_id}`, {
            signal: AbortSignal.timeout(5000),
          })).json()
          if (status.state === 'SUCCESS' || status.state === 'FAILURE') {
            outcome = status.state
            break
          }
        } catch {
          // A slow or dropped request is retried until the deadline.
        }
      }
      await load()
      setMessage(
        outcome === 'SUCCESS' ? 'Inventory updated'
          : outcome === 'FAILURE' ? 'Inventory collection failed; see the error above'
          : 'Still collecting. Refresh the page in a minute to see the result'
      )
    } catch (err) {
      setMessage('Could not reach the backend to collect the inventory')
    } finally {
      setRefreshing(false)
    }
  }

  if (error) return <div className="server-detail"><button onClick={onBack}>← Back to servers</button><p className="sd-error">{error}</p></div>
  if (!detail) return <div className="server-detail sd-loading">Loading server…</div>

  const inv = detail.inventory
  const tabs: { id: Tab; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'applications', label: `Applications (${inv?.applications?.length ?? 0})` },
    { id: 'services', label: `Services (${inv?.services?.length ?? 0})` },
    { id: 'containers', label: `Containers & ports (${inv?.containers?.length ?? 0})` },
    { id: 'patching', label: 'Patching & jobs' },
  ]

  return (
    <div className="server-detail">
      <div className="sd-header">
        <button className="sd-back" onClick={onBack}>← Servers</button>
        <div className="sd-title">
          <h1>{detail.hostname}</h1>
          <span className={`sd-status ${detail.connectivity_status}`}>{detail.connectivity_status}</span>
          <span className="sd-meta">{detail.ip_address || 'no IP'} · {detail.os_type} {detail.os_version || ''}</span>
        </div>
        <button onClick={refreshInventory} disabled={refreshing}>{refreshing ? 'Collecting…' : '↻ Refresh inventory'}</button>
      </div>

      {message && <div className="sd-message">{message}</div>}
      {inv?.error && <div className="sd-error">Last inventory failed: {inv.error}</div>}

      <div className="sd-card sd-tags-notes">
        <div className="sd-field">
          <label>Tags</label>
          <div className="sd-tags">
            {tags.map(tag => (
              <span key={tag} className="sd-tag">
                {tag}
                <button onClick={() => setTags(tags.filter(t => t !== tag))} aria-label={`Remove ${tag}`}>×</button>
              </span>
            ))}
            <input
              className="sd-tag-input"
              placeholder="Add tag and press Enter"
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              onKeyDown={onTagKey}
              onBlur={addTag}
            />
          </div>
        </div>
        <div className="sd-field">
          <label>Notes</label>
          <textarea rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Owner, purpose, maintenance contacts, anything the next operator should know" />
        </div>
        <div className="sd-actions">
          <button className="sd-save" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save tags and notes'}</button>
        </div>
      </div>

      <div className="sd-tabs">
        {tabs.map(t => (
          <button key={t.id} className={tab === t.id ? 'active' : ''} onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
        {onCreateJob && (
          <button className="sd-tab-action" onClick={() => onCreateJob(serverId)}>+ Create job</button>
        )}
      </div>

      {tab === 'overview' && (
        <div className="sd-grid">
          <div className="sd-card">
            <h3>System</h3>
            <Row label="OS" value={inv?.os?.pretty_name || `${detail.os_type} ${detail.os_version || ''}`} />
            <Row label="Kernel" value={inv?.kernel || '—'} />
            <Row label="Uptime" value={inv?.uptime_seconds != null ? `${Math.floor(inv.uptime_seconds / 86400)} day(s)` : '—'} />
            <Row label="Kubernetes node" value={inv?.kubernetes_node ? 'yes' : 'no'} />
            <Row label="Inventory collected" value={formatDateTime(inv?.collected_at)} />
            <Row label="Last SSH check" value={formatDateTime(detail.last_check_at)} />
          </div>
          <div className="sd-card">
            <h3>Health</h3>
            <Row label="CPU" value={detail.health?.cpu_pct != null ? `${detail.health.cpu_pct}%` : '—'} />
            <Row label="Memory" value={detail.health?.memory_pct != null ? `${detail.health.memory_pct}%` : '—'} />
            <Row label="Disk (/)" value={detail.health?.disk_pct != null ? `${detail.health.disk_pct}%` : '—'} />
            <Row label="Load (1m)" value={detail.health?.load_1m != null ? `${detail.health.load_1m} on ${detail.health.cpu_cores} core(s)` : '—'} />
            <Row label="Collected" value={formatDateTime(detail.health?.collected_at)} />
          </div>
          <div className="sd-card">
            <h3>Backup</h3>
            <Row label="Mapped to" value={detail.backup_mapping ? `${detail.backup_mapping.type} ${detail.backup_mapping.vmid} on ${detail.backup_mapping.node}` : 'not mapped'} />
            <Row label="Last backup" value={detail.last_backup ? formatDateTime(detail.last_backup.created_at) : 'none recorded'} />
          </div>
        </div>
      )}

      {tab === 'applications' && (
        <div className="sd-list">
          {(inv?.applications || []).length === 0 && <p className="sd-empty">No known applications detected. Unknown services are listed under Services.</p>}
          {(inv?.applications || []).map(app => (
            <div key={app.id} className="sd-card">
              <div className="sd-app-head">
                <h3>{app.name}</h3>
                <span className={`sd-badge ${app.healthy === false ? 'bad' : app.healthy ? 'good' : ''}`}>
                  {app.healthy === null ? 'no checks' : app.healthy ? 'healthy' : 'unhealthy'}
                </span>
                {app.containerized && <span className="sd-badge">container</span>}
              </div>
              <ul className="sd-checks">
                {app.checks.map(c => (
                  <li key={c.command} className={c.ok ? 'ok' : 'bad'}>
                    <span>{c.ok ? '✓' : '✕'}</span>
                    <code>{c.command}</code>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {tab === 'services' && (
        <div className="sd-card">
          <h3>Running services</h3>
          <ul className="sd-plain">
            {(inv?.services || []).map(s => <li key={s}><code>{s}</code></li>)}
          </ul>
        </div>
      )}

      {tab === 'containers' && (
        <div className="sd-grid">
          <div className="sd-card">
            <h3>Containers</h3>
            {(inv?.containers || []).length === 0 ? <p className="sd-empty">No running containers</p> : (
              <table className="sd-table">
                <thead><tr><th>Name</th><th>Image</th></tr></thead>
                <tbody>{(inv?.containers || []).map(c => <tr key={c.name}><td>{c.name}</td><td><code>{c.image}</code></td></tr>)}</tbody>
              </table>
            )}
          </div>
          <div className="sd-card">
            <h3>Listening ports</h3>
            <ul className="sd-plain">{(inv?.ports || []).map(p => <li key={p}><code>{p}</code></li>)}</ul>
          </div>
        </div>
      )}

      {tab === 'patching' && (
        <div className="sd-grid">
          <div className="sd-card">
            <h3>Patch status</h3>
            <Row label="Pending packages" value={detail.pending_patch_count != null ? String(detail.pending_patch_count) : 'not scanned'} />
            <Row label="Last scan" value={formatDateTime(detail.last_patch_scan_at)} />
            <Row label="Last scan error" value={detail.scan_error || 'none'} />
          </div>
          <div className="sd-card">
            <h3>Recent patch job runs</h3>
            {detail.recent_executions.length === 0 ? <p className="sd-empty">No patch jobs have run on this server</p> : (
              <table className="sd-table">
                <thead><tr><th>Job</th><th>Status</th><th>Ended</th></tr></thead>
                <tbody>
                  {detail.recent_executions.map((e, i) => (
                    <tr key={i}>
                      <td>{e.job_name}</td>
                      <td>{e.status}{e.error ? <div className="sd-err-small">{e.error}</div> : null}</td>
                      <td>{formatDateTime(e.ended)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="sd-row">
      <span className="sd-label">{label}</span>
      <span className="sd-value">{value}</span>
    </div>
  )
}
