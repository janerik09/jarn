import { useEffect, useState } from 'react'
import { Pagination } from './Pagination'
import { useDefaultPageSize } from '../lib/defaultPageSize'
import { formatDateTime } from '../lib/displayTime'
import { getUser } from '../lib/auth'
import './AuditLog.css'

interface AuditEvent {
  seq: number
  created_at: string
  category: string | null
  action: string
  outcome: string | null
  source: string
  actor_name: string | null
  resource_type: string | null
  resource_id: string | null
  correlation_id: string | null
  ip_address: string | null
  reason: string | null
  details: any
  row_hash: string | null
}

interface Summary {
  failed_sign_ins_24h: number
  denied_24h: number
  top_failures: { action: string; category: string; count: number }[]
  by_day: Record<string, Record<string, number>>
}

const CATEGORIES = ['auth', 'access', 'config', 'users', 'job', 'server', 'ssh', 'credential', 'backup', 'proxmox',
  'schedule', 'compliance', 'patch', 'upgrade', 'system', 'audit', 'certificate', 'alert']
const emptyFilters = { q: '', category: '', outcome: '', actor: '', action: '', correlation_id: '', since: '', until: '' }

function queryString(filters: typeof emptyFilters, extra: Record<string, string> = {}) {
  const params = new URLSearchParams()
  Object.entries({ ...filters, ...extra }).forEach(([k, v]) => {
    if (v) params.set(k, k === 'since' || k === 'until' ? new Date(v).toISOString() : v)
  })
  return params.toString()
}

export function AuditLog() {
  const [items, setItems] = useState<AuditEvent[]>([])
  const [total, setTotal] = useState(0)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [filters, setFilters] = useState(emptyFilters)
  const [applied, setApplied] = useState(emptyFilters)
  const [page, setPage] = useState(1)
  const defaultPageSize = useDefaultPageSize()
  const [pageSizeOverride, setPageSize] = useState<number | null>(null)
  const pageSize = pageSizeOverride ?? defaultPageSize
  const [selected, setSelected] = useState<AuditEvent | null>(null)
  const [integrity, setIntegrity] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isAdmin = (getUser()?.level ?? 0) >= 3

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const qs = queryString(applied, { page: String(page), page_size: String(pageSize) })
      const res = await fetch(`/api/v1/audit-logs?${qs}`)
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Could not load the audit trail')
      const data = await res.json()
      setItems(data.items)
      setTotal(data.total)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [applied, page, pageSize])
  useEffect(() => {
    fetch('/api/v1/audit-logs/summary?days=14').then(r => r.json()).then(setSummary).catch(() => setSummary(null))
  }, [])

  const apply = () => { setPage(1); setApplied(filters) }
  const clear = () => { setFilters(emptyFilters); setApplied(emptyFilters); setPage(1) }

  const showJobTrail = (correlation: string) => {
    const next = { ...emptyFilters, correlation_id: correlation }
    setFilters(next)
    setApplied(next)
    setPage(1)
    setSelected(null)
  }

  const checkIntegrity = async () => {
    setIntegrity('Checking…')
    const res = await fetch('/api/v1/audit-logs/verify')
    const data = await res.json()
    setIntegrity(data.ok
      ? `Intact: ${data.checked} events checked, none changed or removed.`
      : `Problem at event ${data.first_break?.seq}: ${data.first_break?.reason}. Events from there on cannot be trusted.`)
  }

  const exportAs = async (format: 'csv' | 'json') => {
    const res = await fetch(`/api/v1/audit-logs/export?format=${format}&${queryString(applied)}`)
    if (!res.ok) { setError('Export failed'); return }
    const url = URL.createObjectURL(await res.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `audit.${format}`
    link.click()
    URL.revokeObjectURL(url)
  }

  const today = new Date().toISOString().slice(0, 10)
  const eventsToday = summary ? Object.entries(summary.by_day).filter(([d]) => d === today).reduce((n, [, o]) => n + Object.values(o).reduce((a, b) => a + b, 0), 0) : 0

  return (
    <div className="audit-page">
      <div className="page-header">
        <h1>Audit Logs</h1>
        <p>Every change, sign-in, refused action and job step, with who did it and what happened.</p>
      </div>

      <div className="audit-summary">
        <div className="audit-card"><span>Failed sign-ins (24h)</span><strong className={summary?.failed_sign_ins_24h ? 'bad' : ''}>{summary?.failed_sign_ins_24h ?? '–'}</strong></div>
        <div className="audit-card"><span>Refused actions (24h)</span><strong className={summary?.denied_24h ? 'warn' : ''}>{summary?.denied_24h ?? '–'}</strong></div>
        <div className="audit-card"><span>Events today</span><strong>{eventsToday}</strong></div>
        <div className="audit-card audit-card-action">
          <button className="btn-edit" onClick={checkIntegrity}>Check integrity</button>
          {integrity && <small>{integrity}</small>}
        </div>
      </div>

      <div className="audit-filters">
        <input placeholder="Search text" value={filters.q} onChange={e => setFilters({ ...filters, q: e.target.value })} />
        <select value={filters.category} onChange={e => setFilters({ ...filters, category: e.target.value })}>
          <option value="">All categories</option>
          {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={filters.outcome} onChange={e => setFilters({ ...filters, outcome: e.target.value })}>
          <option value="">Any outcome</option>
          <option value="success">Success</option>
          <option value="failure">Failure</option>
          <option value="denied">Refused</option>
          <option value="info">Info</option>
        </select>
        <input placeholder="User" value={filters.actor} onChange={e => setFilters({ ...filters, actor: e.target.value })} />
        <input placeholder="Action (e.g. ssh.exec)" value={filters.action} onChange={e => setFilters({ ...filters, action: e.target.value })} />
        <input placeholder="Job or correlation ID" value={filters.correlation_id} onChange={e => setFilters({ ...filters, correlation_id: e.target.value })} />
        <input type="datetime-local" value={filters.since} onChange={e => setFilters({ ...filters, since: e.target.value })} title="From" />
        <input type="datetime-local" value={filters.until} onChange={e => setFilters({ ...filters, until: e.target.value })} title="To" />
        <button className="btn-save-settings" onClick={apply}>Apply</button>
        <button className="btn-cancel" onClick={clear}>Clear</button>
        {isAdmin && <button className="btn-edit" onClick={() => exportAs('csv')}>Export CSV</button>}
        {isAdmin && <button className="btn-edit" onClick={() => exportAs('json')}>Export JSON</button>}
      </div>

      {error && <p className="audit-error">{error}</p>}
      <p className="audit-count">{loading ? 'Loading…' : `${total} matching events`}</p>

      <div className="risk-table-wrap">
        <table className="risk-table audit-table">
          <thead>
            <tr><th>Time (UTC)</th><th>Category</th><th>Action</th><th>Outcome</th><th>User</th><th>Source</th><th>Resource</th><th>Reason</th><th></th></tr>
          </thead>
          <tbody>
            {items.map(e => (
              <tr key={e.seq} className={`audit-row outcome-${e.outcome ?? 'none'}`}>
                <td>{formatDateTime(e.created_at)}</td>
                <td>{e.category}</td>
                <td className="audit-action-cell">{e.action}</td>
                <td><span className={`audit-outcome ${e.outcome ?? ''}`}>{e.outcome ?? '–'}</span></td>
                <td>{e.actor_name ?? '–'}{e.ip_address ? <small> {e.ip_address}</small> : null}</td>
                <td>{e.source}</td>
                <td>{e.resource_type}{e.resource_id ? <small> {e.resource_id.slice(0, 8)}</small> : null}</td>
                <td className="audit-reason">{e.reason ?? ''}</td>
                <td><button className="btn-edit" onClick={() => setSelected(e)}>Details</button></td>
              </tr>
            ))}
            {items.length === 0 && !loading && <tr><td colSpan={9} className="risk-empty">No events match these filters</td></tr>}
          </tbody>
        </table>
      </div>
      <Pagination currentPage={page} totalItems={total} pageSize={pageSize} onPageChange={setPage}
        onPageSizeChange={(size) => { setPageSize(size); setPage(1) }} />

      {selected && (
        <div className="audit-modal" onClick={() => setSelected(null)}>
          <div className="audit-modal-body" onClick={e => e.stopPropagation()}>
            <div className="audit-modal-head">
              <h3>{selected.action}</h3>
              <button className="btn-cancel" onClick={() => setSelected(null)}>Close</button>
            </div>
            <dl className="audit-facts">
              <dt>Time</dt><dd>{formatDateTime(selected.created_at)} (event {selected.seq})</dd>
              <dt>Category</dt><dd>{selected.category}</dd>
              <dt>Outcome</dt><dd>{selected.outcome}</dd>
              <dt>User</dt><dd>{selected.actor_name ?? '–'}</dd>
              <dt>Source address</dt><dd>{selected.ip_address ?? '–'}</dd>
              <dt>Source</dt><dd>{selected.source}</dd>
              <dt>Resource</dt><dd>{selected.resource_type ?? '–'} {selected.resource_id ?? ''}</dd>
              <dt>Reason</dt><dd>{selected.reason ?? '–'}</dd>
              <dt>Correlation</dt><dd>{selected.correlation_id ?? '–'}
                {selected.correlation_id && <button className="btn-edit" onClick={() => showJobTrail(selected.correlation_id!)}>Show all events for this</button>}
              </dd>
              <dt>Integrity hash</dt><dd><code>{selected.row_hash ?? 'not chained'}</code></dd>
            </dl>
            <h4>Detail</h4>
            <pre className="audit-json">{JSON.stringify(selected.details, null, 2)}</pre>
          </div>
        </div>
      )}
    </div>
  )
}
