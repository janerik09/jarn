import { useState, useEffect } from 'react'
import './ComplianceReporting.css'
import { formatDateTime } from '../lib/displayTime'

interface Framework {
  id: string
  name: string
  requirements: number
}

interface FrameworkSummary {
  name: string
  passed: number
  failed: number
  score: number | null
}

interface Assessment {
  id: string
  framework: string
  status: string
  error?: string | null
  created_at: string
  completed_at?: string | null
  overall?: Record<string, number | null> | null
  assessed_hosts?: number | null
  total_hosts?: number | null
}

interface HostRequirement {
  id: string
  title: string
  status: 'passed' | 'failed' | 'not_assessed'
  failed_checks: string[]
}

interface HostResult {
  hostname: string
  ip: string
  status: string
  detail?: string
  checks?: Record<string, { title: string; passed: boolean | null; detail: string }>
  frameworks?: Record<string, { requirements: HostRequirement[] }>
}

interface AssessmentDetail {
  id: string
  framework: string
  status: string
  error?: string | null
  results?: {
    summary: Record<string, FrameworkSummary>
    hosts: HostResult[]
    assessed_hosts: number
    total_hosts: number
    generated_at: string
  } | null
}

const apiBase = () => `/api/v1/compliance`

export function ComplianceReporting() {
  const [frameworks, setFrameworks] = useState<Framework[]>([])
  const [mappingStatus, setMappingStatus] = useState<{ status: string; notes: { framework: string; requirement: string; note: string }[] } | null>(null)
  const [assessments, setAssessments] = useState<Assessment[]>([])
  const [latest, setLatest] = useState<AssessmentDetail | null>(null)
  const [runningId, setRunningId] = useState<string | null>(null)
  const [scope, setScope] = useState<string>('all')
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const loadAssessments = async () => {
    const res = await fetch(`${apiBase()}/assessments`)
    if (res.ok) {
      const data = await res.json()
      setAssessments(data.assessments || [])
      return data.assessments as Assessment[]
    }
    return []
  }

  const loadDetail = async (id: string) => {
    const res = await fetch(`${apiBase()}/assessments/${id}`)
    if (res.ok) {
      const data: AssessmentDetail = await res.json()
      if (data.status === 'completed') setLatest(data)
      return data
    }
    return null
  }

  useEffect(() => {
    fetch('/api/v1/compliance/mappings')
      .then(r => r.json())
      .then(data => {
        const notes: { framework: string; requirement: string; note: string }[] = []
        data.frameworks.forEach((fw: any) => fw.requirements.forEach((req: any) => {
          if (req.note) notes.push({ framework: fw.name, requirement: req.id, note: req.note })
        }))
        setMappingStatus({ status: data.status, notes })
      })
      .catch(() => setMappingStatus(null))
  }, [])

  useEffect(() => {
    const init = async () => {
      try {
        const fw = await fetch(`${apiBase()}/frameworks`)
        if (fw.ok) setFrameworks((await fw.json()).frameworks || [])
        const list = await loadAssessments()
        const completed = list.find(a => a.status === 'completed')
        if (completed) await loadDetail(completed.id)
        const pending = list.find(a => a.status === 'running')
        if (pending) setRunningId(pending.id)
      } catch (err) {
        setError('Could not reach the compliance service')
      } finally {
        setLoading(false)
      }
    }
    init()
  }, [])

  useEffect(() => {
    if (!runningId) return
    const timer = setInterval(async () => {
      const detail = await loadDetail(runningId)
      if (detail && detail.status !== 'running') {
        setRunningId(null)
        if (detail.status === 'failed') setError(detail.error || 'Assessment failed')
        await loadAssessments()
      }
    }, 3000)
    return () => clearInterval(timer)
  }, [runningId])

  const runAssessment = async () => {
    setError(null)
    setStarting(true)
    try {
      const res = await fetch(`${apiBase()}/assessments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ framework: 'all' }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail || 'Could not start the assessment')
        return
      }
      setRunningId(data.id)
      await loadAssessments()
    } catch (err) {
      setError('Could not start the assessment')
    } finally {
      setStarting(false)
    }
  }

  const download = async (assessmentId: string, format: 'pdf' | 'csv', framework: string) => {
    setError(null)
    try {
      const res = await fetch(
        `${apiBase()}/assessments/${assessmentId}/download?format=${format}&framework=${framework}`
      )
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setError(data.detail || 'Download failed')
        return
      }
      const blob = await res.blob()
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `compliance-${framework}.${format}`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)
    } catch (err) {
      setError('Download failed')
    }
  }

  const summaries = latest?.results?.summary || {}
  const visibleFrameworks = scope === 'all' ? Object.keys(summaries) : [scope]

  const failingRequirements = latest?.results
    ? visibleFrameworks.flatMap(fwId => {
        const byReq = new Map<string, { title: string; fw: string; hosts: string[] }>()
        latest.results!.hosts.forEach(host => {
          if (host.status !== 'assessed' || !host.frameworks) return
          host.frameworks[fwId]?.requirements.forEach(req => {
            if (req.status !== 'failed') return
            const entry = byReq.get(req.id) || { title: req.title, fw: fwId, hosts: [] }
            entry.hosts.push(host.hostname)
            byReq.set(req.id, entry)
          })
        })
        return Array.from(byReq.entries()).map(([id, v]) => ({ id, ...v }))
      })
    : []

  const scoreClass = (score: number | null | undefined) => {
    if (score === null || score === undefined) return ''
    if (score >= 90) return 'good'
    if (score >= 70) return 'warn'
    return 'bad'
  }

  return (
    <div className="compliance-page">
      <div className="page-header">
        <h1>Compliance Reports</h1>
        <p className="header-subtitle">
          Checks run on every server over SSH, plus patch-job history, mapped to framework requirements
        </p>
      </div>

      {mappingStatus && (
        <div className="patch-warning" style={{ margin: '12px 0' }}>
          <strong>{mappingStatus.status}.</strong> Scores show what the automated checks found. They are not audit results
          until an auditor has reviewed the requirement mapping.
          {mappingStatus.notes.length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary>Judgement-call mappings ({mappingStatus.notes.length})</summary>
              <ul>
                {mappingStatus.notes.map(n => (
                  <li key={`${n.framework}-${n.requirement}`}>{n.framework} {n.requirement}: {n.note}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {error && (
        <div className="error-message">
          <span>{error}</span>
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}

      <div className="compliance-toolbar">
        <div>
          <label>Show framework</label>
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="all">All frameworks</option>
            {frameworks.map(f => (
              <option key={f.id} value={f.id}>{f.name}</option>
            ))}
          </select>
        </div>
        <button className="btn-generate" onClick={runAssessment} disabled={starting || runningId !== null}>
          {runningId ? '⟳ Assessment running…' : starting ? 'Starting…' : '▶ Run assessment'}
        </button>
      </div>

      {loading && <p>Loading…</p>}

      {!loading && !latest && !runningId && (
        <div className="empty-state">
          <p>No completed assessment yet</p>
          <p className="empty-hint">Run an assessment to check every server against the frameworks</p>
        </div>
      )}

      {latest?.results && (
        <>
          <p className="header-subtitle">
            Latest completed assessment: {latest.results.assessed_hosts} of {latest.results.total_hosts} servers assessed,
            generated {formatDateTime(latest.results.generated_at + 'Z')}
          </p>

          <div className="score-grid">
            {visibleFrameworks.map(fwId => {
              const s = summaries[fwId]
              return (
                <div key={fwId} className="score-card">
                  <div className="framework-name">{s.name}</div>
                  <div className={`score-value ${scoreClass(s.score)}`}>
                    {s.score === null ? 'n/a' : `${s.score}%`}
                  </div>
                  <div className="score-detail">{s.passed} passed · {s.failed} failed requirement checks</div>
                  <div className="score-actions">
                    <button onClick={() => download(latest.id, 'pdf', fwId)}>PDF</button>
                    <button onClick={() => download(latest.id, 'csv', fwId)}>CSV</button>
                  </div>
                </div>
              )
            })}
          </div>

          <h3>Failing requirements</h3>
          {failingRequirements.length === 0 ? (
            <p className="empty-hint">No failing requirements in the selected frameworks</p>
          ) : (
            <table className="compliance-table">
              <thead>
                <tr><th>Framework</th><th>Requirement</th><th>Servers failing</th></tr>
              </thead>
              <tbody>
                {failingRequirements.map(r => (
                  <tr key={`${r.fw}-${r.id}`}>
                    <td>{summaries[r.fw]?.name}</td>
                    <td><code>{r.id}</code> {r.title}</td>
                    <td title={r.hosts.join(', ')}>{r.hosts.length} of {latest.results!.assessed_hosts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3>Per server</h3>
          <table className="compliance-table">
            <thead>
              <tr><th>Server</th><th>Status</th><th>OS</th><th>Failed checks</th></tr>
            </thead>
            <tbody>
              {latest.results.hosts.map(host => {
                const failed = host.checks
                  ? Object.values(host.checks).filter(c => c.passed === false).map(c => c.title)
                  : []
                return (
                  <tr key={host.ip}>
                    <td>{host.hostname}</td>
                    <td>{host.status}</td>
                    <td>{host.detail || ''}</td>
                    <td>{host.status === 'assessed' ? (failed.length ? failed.join('; ') : 'none') : ''}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}

      <h3>Assessment history</h3>
      {assessments.length === 0 ? (
        <p className="empty-hint">No assessments yet</p>
      ) : (
        <table className="compliance-table">
          <thead>
            <tr><th>Run</th><th>Status</th><th>Servers</th><th>Downloads</th></tr>
          </thead>
          <tbody>
            {assessments.map(a => (
              <tr key={a.id}>
                <td>{formatDateTime(a.created_at + 'Z')}</td>
                <td>
                  {a.status}
                  {a.error && <div className="empty-hint">{a.error}</div>}
                </td>
                <td>{a.assessed_hosts ?? '—'} of {a.total_hosts ?? '—'}</td>
                <td>
                  {a.status === 'completed' && (
                    <>
                      <button onClick={() => download(a.id, 'pdf', 'all')}>PDF</button>{' '}
                      <button onClick={() => download(a.id, 'csv', 'all')}>CSV</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="empty-hint">
        Control mappings are a starting point. Validate the requirement references with your auditor before
        using a report as evidence.
      </p>
    </div>
  )
}
