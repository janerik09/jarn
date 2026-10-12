import { getAccessToken } from '../lib/auth'
import { useState, useEffect } from 'react'
import './JobMonitor.css'
import { formatDateTime, formatTime } from '../lib/displayTime'

interface JobStats {
  total: number
  succeeded: number
  failed: number
  rolled_back?: number
  executing: number
  pending: number
}

type ServerState = 'pending' | 'executing' | 'succeeded' | 'failed' | 'rolled_back' | 'skipped'

// The feed sends the stored execution status ("success"); the screen uses "succeeded"
const toServerState = (raw: string): ServerState => (raw === 'success' ? 'succeeded' : (raw as ServerState))

interface PreflightResult {
  ready: boolean
  checked_at: string
  results: Record<string, { ready: boolean; checks: Record<string, string>; blockers?: string[] }>
}

interface RollbackPreview {
  eligible: { hostname: string; packages: string[]; kernel_left_installed: string[] }[]
  skipped: { hostname: string; reason: string }[]
}

interface ServerPhase {
  name: string
  status: 'pending' | 'active' | 'completed' | 'failed'
  duration_ms?: number
  message?: string
}

interface ServerStatus {
  server_id: string
  hostname: string
  status: ServerState
  phase?: number
  phases: ServerPhase[]
  progress?: string
  duration_ms?: number
  error?: string
}

interface JobUpdate {
  type: string
  job_id: string
  job_status?: string
  timestamp: string
  awaiting_continue?: boolean
  stats?: JobStats
  servers?: ServerStatus[]
}

interface JobMonitorProps {
  jobId: string
}

interface PhaseLog {
  name: string
  status: string
  timestamp: string
  output: string
  error: string
}

const DEPLOYMENT_PHASES = [
  { id: 0, name: 'Health Check', icon: '🏥' },
  { id: 1, name: 'Pre-Install', icon: '📋' },
  { id: 2, name: 'Install', icon: '📦' },
  { id: 3, name: 'Validation', icon: '✓' },
  { id: 4, name: 'Rollback', icon: '↩️' },
]

export function JobMonitor({ jobId }: JobMonitorProps) {
  const [stats, setStats] = useState<JobStats>({
    total: 0,
    succeeded: 0,
    failed: 0,
    executing: 0,
    pending: 0,
  })
  const [servers, setServers] = useState<ServerStatus[]>([])
  const [connected, setConnected] = useState(false)
  const [startTime] = useState(new Date())
  const [logs, setLogs] = useState<string[]>([])
  const [awaitingContinue, setAwaitingContinue] = useState(false)
  const [continuing, setContinuing] = useState(false)
  const [serverMap, setServerMap] = useState<Record<string, string>>({})
  const [logFilter, setLogFilter] = useState<'all' | 'errors' | 'info'>('all')
  const [jobStatus, setJobStatus] = useState<string>('')
  const [preflight, setPreflight] = useState<PreflightResult | null>(null)
  const [preflightBusy, setPreflightBusy] = useState(false)
  const [rollbackPreview, setRollbackPreview] = useState<RollbackPreview | null>(null)
  const [rollbackMessage, setRollbackMessage] = useState<string | null>(null)
  const [expandedServer, setExpandedServer] = useState<string | null>(null)
  const [selectedPhase, setSelectedPhase] = useState<{ serverId: string; phaseName: string } | null>(null)
  const [phaseLogs, setPhaseLogs] = useState<PhaseLog | null>(null)
  const [loadingPhase, setLoadingPhase] = useState(false)

  useEffect(() => {
    const fetchServers = async () => {
      try {
        const response = await fetch(`/api/v1/servers`)
        const data = await response.json()
        const map: Record<string, string> = {}
        data.forEach((server: any) => {
          map[server.id] = server.hostname
        })
        setServerMap(map)
      } catch (err) {
        console.error('Failed to fetch servers:', err)
      }
    }
    fetchServers()
  }, [])

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = getAccessToken() ?? ''
    const ws = new WebSocket(`${protocol}//${window.location.host}/api/v1/jobs/ws/${jobId}/monitor?token=${encodeURIComponent(token)}`)

    ws.onopen = () => {
      setConnected(true)
      setLogs(prev => [...prev, `[${formatTime(new Date())}] ✓ Connected to job monitor`])
    }

    ws.onmessage = (event) => {
      try {
        const update: JobUpdate = JSON.parse(event.data)

        if (update.job_status) setJobStatus(update.job_status)
        if (update.type === 'progress' && update.stats) {
          setStats(update.stats)
          setAwaitingContinue(Boolean(update.awaiting_continue))
        }

        if (update.servers && Array.isArray(update.servers)) {
          const updatedServers = update.servers.map((server: any) => ({
            ...server,
            status: toServerState(server.status),
            hostname: serverMap[server.server_id] || server.hostname || server.server_id,
            phases: server.phases || [],
          }))
          setServers(updatedServers)
        }
      } catch (e) {
        console.error('Failed to parse update:', e)
      }
    }

    ws.onerror = () => {
      setLogs(prev => [...prev, `[${formatTime(new Date())}] ✕ Connection error`])
    }

    ws.onclose = () => {
      setConnected(false)
      setLogs(prev => [...prev, `[${formatTime(new Date())}] ● Disconnected`])
    }

    return () => ws.close()
  }, [jobId, serverMap])

  const progressPercent = stats.total > 0
    ? Math.round(((stats.succeeded + stats.failed) / stats.total) * 100)
    : 0

  const elapsedSeconds = Math.round((new Date().getTime() - startTime.getTime()) / 1000)
  const completedCount = stats.succeeded + stats.failed
  const estimatedTotal = stats.total > 0 && completedCount > 0
    ? Math.round((elapsedSeconds / completedCount) * stats.total)
    : 0

  const handleStartJob = async () => {
    try {
      const response = await fetch(`/api/v1/jobs/${jobId}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
      if (!response.ok) {
        const data = await response.json()
        alert(`Error: ${data.detail || 'Failed to start job'}`)
      } else {
        window.location.reload()
      }
    } catch (err) {
      alert(`Error: ${err instanceof Error ? err.message : 'Failed to start job'}`)
    }
  }

  const runPreflight = async () => {
    setPreflightBusy(true)
    try {
      const response = await fetch(`/api/v1/jobs/${jobId}/preflight-check`, { method: 'POST' })
      const data = await response.json()
      if (!response.ok) alert(`Error: ${data.detail || 'Pre-flight check failed'}`)
      else setPreflight(data)
    } catch (err) {
      alert(`Error: ${err instanceof Error ? err.message : 'Pre-flight check failed'}`)
    } finally {
      setPreflightBusy(false)
    }
  }

  const previewRollback = async () => {
    setRollbackMessage(null)
    const response = await fetch(`/api/v1/jobs/${jobId}/rollback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: false }),
    })
    const data = await response.json()
    if (!response.ok) alert(`Error: ${data.detail || 'Could not prepare the rollback'}`)
    else setRollbackPreview(data)
  }

  const confirmRollback = async () => {
    const response = await fetch(`/api/v1/jobs/${jobId}/rollback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true }),
    })
    const data = await response.json()
    if (!response.ok) {
      alert(`Error: ${data.detail || 'Rollback failed to start'}`)
      return
    }
    setRollbackPreview(null)
    setRollbackMessage(`Rollback started on ${data.servers} server(s). Refresh this page to see each server's result.`)
  }

  const handleRetryFailed = async () => {
    try {
      const response = await fetch(`/api/v1/jobs/${jobId}/retry-failed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
      const data = await response.json()
      if (!response.ok) {
        alert(`Error: ${data.detail || 'Failed to retry job'}`)
      } else {
        window.location.href = `/?view=job-monitor&jobId=${data.id}`
      }
    } catch (err) {
      alert(`Error: ${err instanceof Error ? err.message : 'Failed to retry job'}`)
    }
  }

  const getBackendPhaseName = (frontendName: string): string => {
    const mapping: Record<string, string> = {
      'Health Check': 'pre_patch_health_check',
      'Pre-Install': 'patch_safety_analysis',
      'Install': 'install_patches',
      'Validation': 'post_patch_health_check',
      'Rollback': 'auto_rollback',
    }
    return mapping[frontendName] || frontendName
  }

  const getPhaseByName = (server: any, phaseName: string) => {
    if (!server.phases || !Array.isArray(server.phases)) return undefined
    const backendName = getBackendPhaseName(phaseName)
    return server.phases.find((p: any) => p.name === backendName)
  }

  const handlePhaseClick = async (serverId: string, phaseName: string) => {
    setSelectedPhase({ serverId, phaseName })
    setLoadingPhase(true)
    try {
      // Fetch execution details with metadata
      const response = await fetch(
        `/api/v1/jobs/${jobId}/executions?server_id=${serverId}`
      )
      if (response.ok) {
        const data = await response.json()
        const execution = data.executions?.[0]
        if (execution?.metadata_?.phases) {
          const backendName = getBackendPhaseName(phaseName)
          const phase = execution.metadata_.phases.find((p: PhaseLog) => p.name === backendName)
          setPhaseLogs(phase || null)
        }
      }
    } catch (err) {
      console.error('Failed to fetch phase logs:', err)
    } finally {
      setLoadingPhase(false)
    }
  }

  const filteredLogs = logs.filter(log => {
    if (logFilter === 'errors') return log.includes('✕') || log.includes('error')
    if (logFilter === 'info') return log.includes('✓')
    return true
  })

  return (
    <div className="job-monitor-enhanced">
      <div className="monitor-header">
        <div>
          <h1>Deployment Progress: {jobId.slice(0, 8)}</h1>
          <p className="monitor-subtitle">Real-time monitoring with per-server phase tracking</p>
        </div>
        <div className="monitor-controls">
          <div className={`connection-badge ${connected ? 'connected' : 'disconnected'}`}>
            ● {connected ? 'Connected' : 'Disconnected'}
          </div>
          {stats.pending > 0 && stats.executing === 0 && !awaitingContinue && (
            <button className="btn-start" onClick={handleStartJob}>▶ Start Deployment</button>
          )}
          {awaitingContinue && (
            <button
              className="btn-start"
              disabled={continuing}
              onClick={async () => {
                setContinuing(true)
                try {
                  await fetch(`/api/v1/jobs/${jobId}/continue`, { method: 'POST' })
                } finally {
                  setContinuing(false)
                }
              }}
            >
              ▶ Continue rollout
            </button>
          )}
          {stats.pending > 0 && stats.executing === 0 && (
            <button className="btn-edit" onClick={runPreflight} disabled={preflightBusy}>
              {preflightBusy ? 'Checking…' : 'Pre-flight check'}
            </button>
          )}
          {(jobStatus === 'completed' || jobStatus === 'rolled_back') && stats.executing === 0 && stats.succeeded > 0 && (
            <button className="btn-edit" onClick={previewRollback}>↺ Roll back job</button>
          )}
          {stats.failed > 0 && (
            <button className="btn-retry" onClick={handleRetryFailed}>↻ Retry Failed ({stats.failed})</button>
          )}
        </div>
      </div>
      {preflight && (
        <div className="patch-warning" style={{ margin: '12px 0' }}>
          <strong>Pre-flight: {preflight.ready ? 'all servers ready' : 'not ready'}</strong>
          <span style={{ marginLeft: 8, opacity: 0.7 }}>checked {formatDateTime(preflight.checked_at)}</span>
          <button className="btn-cancel" style={{ float: 'right' }} onClick={() => setPreflight(null)}>Close</button>
          {Object.entries(preflight.results).map(([serverId, result]) => (
            <div key={serverId} style={{ marginTop: 8 }}>
              <div>{result.ready ? '✓' : '✕'} <strong>{serverMap[serverId] || serverId}</strong></div>
              <ul style={{ margin: '4px 0 0 18px', fontSize: '0.85em' }}>
                {Object.entries(result.checks).map(([name, text]) => <li key={name}>{name}: {text}</li>)}
              </ul>
            </div>
          ))}
        </div>
      )}

      {rollbackMessage && (
        <div className="patch-warning" style={{ margin: '12px 0' }}>{rollbackMessage}</div>
      )}

      {rollbackPreview && (
        <div className="patch-warning" style={{ margin: '12px 0' }}>
          <strong>Roll back {rollbackPreview.eligible.length} server(s)?</strong>
          <p style={{ margin: '6px 0' }}>
            Each server's packages are downgraded to the versions recorded before this job. Reboots are not undone, and
            kernel packages are never downgraded by package.
          </p>
          <ul style={{ margin: '4px 0 0 18px', fontSize: '0.85em' }}>
            {rollbackPreview.eligible.map(item => (
              <li key={item.hostname}>
                {item.hostname}: {item.packages.join(', ')}
                {item.kernel_left_installed.length > 0 && ` (kernel left as installed: ${item.kernel_left_installed.join(', ')})`}
              </li>
            ))}
          </ul>
          {rollbackPreview.skipped.length > 0 && (
            <>
              <p style={{ margin: '8px 0 4px' }}>Not rolled back:</p>
              <ul style={{ margin: '0 0 0 18px', fontSize: '0.85em' }}>
                {rollbackPreview.skipped.map(item => <li key={item.hostname}>{item.hostname}: {item.reason}</li>)}
              </ul>
            </>
          )}
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button className="btn-retry" onClick={confirmRollback} disabled={rollbackPreview.eligible.length === 0}>
              Roll back {rollbackPreview.eligible.length} server(s)
            </button>
            <button className="btn-cancel" onClick={() => setRollbackPreview(null)}>Cancel</button>
          </div>
        </div>
      )}

      {awaitingContinue && (
        <div className="patch-warning" style={{ margin: '12px 0' }}>
          The canary servers finished without failures. The rest of the rollout is paused until you review the canary
          results and choose Continue rollout.
        </div>
      )}

      {/* Progress Overview */}
      <div className="progress-overview">
        <div className="progress-main">
          <div className="progress-bar-container">
            <div className="progress-bar-track">
              <div className="progress-bar-fill" style={{ width: `${progressPercent}%` }} />
            </div>
            <div className="progress-labels">
              <span className="progress-percent">{progressPercent}%</span>
              <span className="progress-time">{elapsedSeconds}s / ~{estimatedTotal}s</span>
            </div>
          </div>
        </div>

        <div className="stats-cards">
          <div className="stat-card total">
            <div className="stat-icon">🖥️</div>
            <div className="stat-info">
              <div className="stat-value">{stats.total}</div>
              <div className="stat-label">Total</div>
            </div>
          </div>
          <div className="stat-card success">
            <div className="stat-icon">✓</div>
            <div className="stat-info">
              <div className="stat-value">{stats.succeeded}</div>
              <div className="stat-label">Succeeded</div>
            </div>
          </div>
          <div className="stat-card executing">
            <div className="stat-icon">⚡</div>
            <div className="stat-info">
              <div className="stat-value">{stats.executing}</div>
              <div className="stat-label">Running</div>
            </div>
          </div>
          <div className="stat-card failed">
            <div className="stat-icon">✕</div>
            <div className="stat-info">
              <div className="stat-value">{stats.failed}</div>
              <div className="stat-label">Failed</div>
            </div>
          </div>
          <div className="stat-card pending">
            <div className="stat-icon">⏳</div>
            <div className="stat-info">
              <div className="stat-value">{stats.pending}</div>
              <div className="stat-label">Pending</div>
            </div>
          </div>
        </div>
      </div>

      {/* Deployment Timeline */}
      <div className="deployment-phases">
        <h3>Deployment Phases</h3>
        <div className="phases-timeline">
          {DEPLOYMENT_PHASES.map(phase => (
            <div key={phase.id} className="phase-indicator">
              <div className="phase-icon">{phase.icon}</div>
              <div className="phase-name">{phase.name}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Server Status Grid */}
      <div className="servers-grid-section">
        <h3>Server Deployment Status</h3>
        <div className="servers-grid">
          {servers.map(server => (
            <div
              key={server.server_id}
              className={`server-card status-${server.status}`}
              onClick={() => setExpandedServer(expandedServer === server.server_id ? null : server.server_id)}
            >
              <div className="card-header">
                <div className="status-icon">{
                  server.status === 'succeeded' ? '✓' :
                  server.status === 'failed' ? '✕' :
                  server.status === 'rolled_back' ? '↺' :
                  server.status === 'skipped' ? '–' :
                  server.status === 'executing' ? '⚡' : '⏳'
                }</div>
                <div className="card-title">
                  <div className="server-name">{server.hostname}</div>
                  <div className="server-status">{server.status}</div>
                </div>
              </div>

              {expandedServer === server.server_id && (
                <div className="card-expanded">
                  {(server.phases || []).some((p: any) => ['readiness_check', 'backup', 'distro_upgrade'].includes(p.name)) ? (
                    <div className="phases-track">
                      {server.phases.map((p: any) => {
                        const cls = ['success', 'ready', 'risky'].includes(p.status) ? 'completed'
                          : p.status === 'executing' ? 'active'
                          : ['failed', 'blocked', 'not_supported'].includes(p.status) ? 'failed' : 'pending'
                        return (
                          <div key={p.name} className={`phase-step ${cls}`}>
                            <div className="phase-dot">{cls === 'completed' ? '✓' : cls === 'failed' ? '✕' : cls === 'active' ? '⚡' : '⏳'}</div>
                            <div className="phase-info">
                              <div className="phase-label">{p.name.replace(/_/g, ' ')}</div>
                              {p.error && <div className="phase-status failed">{p.error}</div>}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  ) : (
                  <div className="phases-track">
                    {DEPLOYMENT_PHASES.map((phase) => {
                      const serverPhase = getPhaseByName(server, phase.name)
                      const phaseStatus = serverPhase?.status || 'pending'
                      const isCompleted = phaseStatus === 'success'
                      const isActive = phaseStatus === 'executing'
                      const isFailed = phaseStatus === 'failed'
                      const isSelected = selectedPhase?.serverId === server.server_id && selectedPhase?.phaseName === phase.name
                      return (
                        <div
                          key={phase.id}
                          className={`phase-step ${isCompleted ? 'completed' : isActive ? 'active' : isFailed ? 'failed' : 'pending'} ${isSelected ? 'selected' : ''}`}
                          onClick={() => handlePhaseClick(server.server_id, phase.name)}
                          style={{ cursor: 'pointer' }}
                        >
                          <div className="phase-dot">{phase.icon}</div>
                          <div className="phase-info">
                            <div className="phase-label">{phase.name}</div>
                            {serverPhase?.status && (
                              <div className={`phase-status ${phaseStatus}`}>{phaseStatus}</div>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  )}
                  {server.error && (
                    <div className="error-message">⚠️ {server.error}</div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Phase Details Modal */}
      {selectedPhase && (
        <div className="phase-details-overlay" onClick={() => setSelectedPhase(null)}>
          <div className="phase-details-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Phase Details: {selectedPhase.phaseName}</h3>
              <button className="close-btn" onClick={() => setSelectedPhase(null)}>✕</button>
            </div>
            <div className="modal-content">
              {loadingPhase ? (
                <div className="loading">Loading phase details...</div>
              ) : phaseLogs ? (
                <>
                  <div className="phase-detail-item">
                    <label>Status:</label>
                    <span className={`status-badge status-${phaseLogs.status}`}>
                      {phaseLogs.status === 'success' ? '✓' : '✕'} {phaseLogs.status}
                    </span>
                  </div>
                  <div className="phase-detail-item">
                    <label>Timestamp:</label>
                    <span>{formatDateTime(phaseLogs.timestamp)}</span>
                  </div>
                  {phaseLogs.output && (
                    <div className="phase-detail-item full-width">
                      <label>Output:</label>
                      <div className="phase-output">{phaseLogs.output}</div>
                    </div>
                  )}
                  {phaseLogs.error && (
                    <div className="phase-detail-item full-width error-section">
                      <label>Error:</label>
                      <div className="phase-error">{phaseLogs.error}</div>
                    </div>
                  )}
                </>
              ) : (
                <div className="no-data">No phase details available</div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Live Log Viewer */}
      <div className="logs-viewer">
        <div className="logs-header">
          <h3>Live Deployment Log</h3>
          <div className="log-filters">
            {(['all', 'info', 'errors'] as const).map(filter => (
              <button
                key={filter}
                className={`filter-btn ${logFilter === filter ? 'active' : ''}`}
                onClick={() => setLogFilter(filter)}
              >
                {filter === 'all' ? 'All' : filter === 'info' ? '✓ Info' : '✕ Errors'}
              </button>
            ))}
          </div>
        </div>
        <div className="log-window">
          {filteredLogs.length === 0 ? (
            <div className="log-empty">No {logFilter === 'errors' ? 'errors' : 'logs'} to display</div>
          ) : (
            filteredLogs.map((log, i) => (
              <div key={i} className={`log-line ${log.includes('✕') ? 'error' : log.includes('✓') ? 'success' : ''}`}>
                {log}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
