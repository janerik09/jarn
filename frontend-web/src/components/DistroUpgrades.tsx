import { useState, useEffect } from 'react'
import './DistroUpgrades.css'
import { Pagination } from './Pagination'
import { useDefaultPageSize } from '../lib/defaultPageSize'

interface Server {
  id: string
  hostname: string
  os_type: string
  os_version: string
  connectivity_status: string
}

interface ExcludedServer {
  id: string
  hostname: string
  reason: string
}

interface UpgradeOptions {
  server_id: string
  hostname: string
  current_distro: string
  current_version: string
  available_upgrades: string[]
  pre_upgrade_steps: string[]
  post_upgrade_steps: string[]
}

interface AnalysisResult {
  server_id: string
  hostname: string
  analysis: {
    current_distro: string
    target_distro: string
    upgrade_status: string
    warnings: string[]
    blockers: string[]
    recommendations: string[]
    estimated_duration_minutes: number
    disk_space_required_gb: number
    rollback_plan: any
  }
  running_services: string[]
  critical_services: string[]
}

export function DistroUpgrades() {
  const [servers, setServers] = useState<Server[]>([])
  const [excluded, setExcluded] = useState<ExcludedServer[]>([])
  const [selectedServer, setSelectedServer] = useState<string | null>(null)
  const [upgradeOptions, setUpgradeOptions] = useState<UpgradeOptions | null>(null)
  const [selectedTarget, setSelectedTarget] = useState<string | null>(null)
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [step, setStep] = useState<'select' | 'options' | 'analyze' | 'review'>('select')
  const [currentPage, setCurrentPage] = useState(1)
  const defaultPageSize = useDefaultPageSize()
  const [pageSizeOverride, setPageSize] = useState<number | null>(null)
  const pageSize = pageSizeOverride ?? defaultPageSize
  const [confirmUpgrade, setConfirmUpgrade] = useState(false)
  const [queuedMessage, setQueuedMessage] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  const handleStartUpgrade = async () => {
    if (!selectedServer || !selectedTarget || !analysis) return
    setStarting(true)
    setError(null)
    try {
      const response = await fetch(
        `/api/v1/servers/${selectedServer}/start-upgrade`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            target_version: selectedTarget,
            confirm: confirmUpgrade,
            acknowledge_risks: analysis.analysis.upgrade_status === 'risky',
          }),
        }
      )
      const data = await response.json()
      if (!response.ok) {
        setError(data.detail || 'Failed to start upgrade')
        return
      }
      setQueuedMessage(`${data.message} Job ID: ${data.job_id}`)
    } catch (err) {
      setError('Failed to start upgrade')
      console.error(err)
    } finally {
      setStarting(false)
    }
  }

  useEffect(() => {
    fetchServers()
  }, [])

  const fetchServers = async () => {
    try {
      const response = await fetch(`/api/v1/distro-upgrades/candidates`)
      const data = await response.json()
      setServers(data.candidates)
      setExcluded(data.excluded)
    } catch (err) {
      setError('Failed to load servers')
      console.error(err)
    }
  }

  const handleSelectServer = async (serverId: string) => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch(`/api/v1/servers/${serverId}/upgrade-options`)
      const data = await response.json()
      setUpgradeOptions(data)
      setSelectedServer(serverId)
      setStep('options')
    } catch (err) {
      setError('Failed to load upgrade options')
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const handleSelectTarget = async (targetVersion: string) => {
    setLoading(true)
    setError(null)
    setSelectedTarget(targetVersion)
    try {
      const response = await fetch(
        `/api/v1/servers/${selectedServer}/analyze-upgrade`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_version: targetVersion }),
        }
      )
      const data = await response.json()
      if (!response.ok) {
        setError(data.detail || 'Upgrade analysis failed')
        return
      }
      setAnalysis(data)
      setStep('analyze')
    } catch (err) {
      setError('Failed to analyze upgrade')
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const getStatusColor = (status: string): string => {
    if (status === 'ready') return '#3a9d6e'
    if (status === 'risky') return '#d4a132'
    if (status === 'blocked') return '#c8514c'
    if (status === 'not_supported') return '#8b95a5'
    return '#e6ebf1'
  }

  const getStatusLabel = (status: string): string => {
    if (status === 'ready') return '✓ Ready'
    if (status === 'risky') return '⚠ Risky'
    if (status === 'blocked') return '✕ Blocked'
    if (status === 'not_supported') return '✗ Not Supported'
    return status
  }

  return (
    <div className="distro-upgrades-container">
      <div className="page-header">
        <h1>Distro Upgrades</h1>
        <p>Safely upgrade operating system versions with comprehensive safety checks</p>
      </div>

      {error && (
        <div className="error-message">
          <span>{error}</span>
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {/* Step 1: Select Server */}
      {step === 'select' && (
        <div className="upgrade-step">
          <h2>Select Server</h2>
          {servers.length === 0 && (
            <p className="upgrade-empty">No servers can start a distro upgrade right now.</p>
          )}
          <div className="distro-server-grid">
            {servers.slice((currentPage - 1) * pageSize, currentPage * pageSize).map(server => (
              <button
                key={server.id}
                className={`server-card ${server.connectivity_status === 'healthy' ? '' : 'unreachable'}`}
                onClick={() => handleSelectServer(server.id)}
                disabled={server.connectivity_status !== 'healthy'}
              >
                <div className="server-card-header">
                  <span className="server-name">🖥️ {server.hostname}</span>
                  <span className={`status-badge status-${server.connectivity_status}`}>
                    {server.connectivity_status === 'healthy' ? '● Healthy' : '● Unreachable'}
                  </span>
                </div>
                <div className="server-card-info">
                  <span>{server.os_type} {server.os_version}</span>
                </div>
              </button>
            ))}
          </div>
          {excluded.length > 0 && (
            <details className="excluded-servers">
              <summary>
                {excluded.length} server{excluded.length === 1 ? '' : 's'} not listed
              </summary>
              <ul>
                {excluded.map(item => (
                  <li key={item.id}>
                    <span className="excluded-host">{item.hostname}</span>
                    <span className="excluded-reason">{item.reason}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {servers.length > 0 && (
            <Pagination
              currentPage={currentPage}
              totalItems={servers.length}
              pageSize={pageSize}
              onPageChange={setCurrentPage}
              onPageSizeChange={(size) => {
                setPageSize(size)
                setCurrentPage(1)
              }}
            />
          )}
        </div>
      )}

      {/* Step 2: Select Target Version */}
      {step === 'options' && upgradeOptions && (
        <div className="upgrade-step">
          <button className="back-btn" onClick={() => setStep('select')}>
            ← Back to Server Selection
          </button>
          <h2>Upgrade Options for {upgradeOptions.hostname}</h2>
          <p>Current: {upgradeOptions.current_distro}</p>

          {upgradeOptions.available_upgrades.length === 0 ? (
            <div className="no-upgrades">
              <p>✓ This server is already running the latest supported version</p>
            </div>
          ) : (
            <>
              <h3>Available Upgrade Paths</h3>
              <div className="upgrade-targets">
                {upgradeOptions.available_upgrades.map(target => (
                  <button
                    key={target}
                    className="upgrade-target-btn"
                    onClick={() => handleSelectTarget(target.split('-')[1])}
                    disabled={loading}
                  >
                    <span className="target-name">→ {target}</span>
                    <span className="arrow">→</span>
                  </button>
                ))}
              </div>

              <div className="upgrade-guide">
                <h3>Pre-Upgrade Checklist</h3>
                <ul>
                  {upgradeOptions.pre_upgrade_steps.map((step, idx) => (
                    <li key={idx}>{step}</li>
                  ))}
                </ul>

                <h3>Post-Upgrade Verification</h3>
                <ul>
                  {upgradeOptions.post_upgrade_steps.map((step, idx) => (
                    <li key={idx}>{step}</li>
                  ))}
                </ul>
              </div>
            </>
          )}
        </div>
      )}

      {/* Step 3: Analyze Upgrade */}
      {step === 'analyze' && analysis && (
        <div className="upgrade-step">
          <button className="back-btn" onClick={() => setStep('options')}>
            ← Back to Upgrade Options
          </button>
          <h2>Upgrade Analysis</h2>
          <p>Server: {analysis.hostname} | Target: {analysis.analysis.target_distro}</p>

          <div className="analysis-card">
            <div className="status-section">
              <div className="status-display">
                <div
                  className="status-badge"
                  style={{ color: getStatusColor(analysis.analysis.upgrade_status) }}
                >
                  {getStatusLabel(analysis.analysis.upgrade_status)}
                </div>
              </div>

              {analysis.analysis.blockers.length > 0 && (
                <div className="blockers-section">
                  <h3 style={{ color: '#c8514c' }}>🚫 Blockers</h3>
                  <ul>
                    {analysis.analysis.blockers.map((blocker, idx) => (
                      <li key={idx} style={{ color: '#c8514c' }}>{blocker}</li>
                    ))}
                  </ul>
                </div>
              )}

              {analysis.analysis.warnings.length > 0 && (
                <div className="warnings-section">
                  <h3 style={{ color: '#d4a132' }}>⚠️ Warnings</h3>
                  <ul>
                    {analysis.analysis.warnings.map((warning, idx) => (
                      <li key={idx} style={{ color: '#d4a132' }}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}

              {analysis.analysis.recommendations.length > 0 && (
                <div className="recommendations-section">
                  <h3 style={{ color: '#3a9d6e' }}>✓ Recommendations</h3>
                  <ul>
                    {analysis.analysis.recommendations.map((rec, idx) => (
                      <li key={idx} style={{ color: '#3a9d6e' }}>{rec}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            <div className="analysis-details">
              <div className="detail-item">
                <span className="label">Estimated Duration:</span>
                <span className="value">{analysis.analysis.estimated_duration_minutes} minutes</span>
              </div>
              <div className="detail-item">
                <span className="label">Disk Space Required:</span>
                <span className="value">{analysis.analysis.disk_space_required_gb} GB</span>
              </div>
              <div className="detail-item">
                <span className="label">Running Critical Services:</span>
                <span className="value">{analysis.critical_services.join(', ') || 'None'}</span>
              </div>
            </div>

            {analysis.analysis.rollback_plan && (
              <div className="rollback-section">
                <h3>Rollback Plan</h3>
                <p><strong>Method:</strong> {analysis.analysis.rollback_plan.method}</p>
                <p><strong>Difficulty:</strong> {analysis.analysis.rollback_plan.difficulty}</p>
                <p><strong>Estimated Time:</strong> {analysis.analysis.rollback_plan.estimated_time_minutes} minutes</p>
                <p><strong>Note:</strong> {analysis.analysis.rollback_plan.notes}</p>
              </div>
            )}
          </div>

          {analysis.analysis.upgrade_status === 'blocked' ? (
            <div className="action-message blocked">
              ✕ Upgrade is blocked. Please resolve blockers before proceeding.
            </div>
          ) : analysis.analysis.upgrade_status === 'ready' ? (
            <div className="action-buttons">
              <button
                className="btn-schedule-upgrade"
                onClick={() => setStep('review')}
              >
                ✓ Proceed to Schedule Upgrade
              </button>
            </div>
          ) : (
            <div className="action-buttons">
              <button
                className="btn-risky-upgrade"
                onClick={() => setStep('review')}
              >
                ⚠️ Acknowledge Risks & Proceed
              </button>
            </div>
          )}
        </div>
      )}

      {/* Step 4: Review & Schedule */}
      {step === 'review' && analysis && (
        <div className="upgrade-step">
          <h2>Schedule Upgrade</h2>
          <p>Ready to schedule upgrade for {analysis.hostname} to {analysis.analysis.target_distro}</p>

          <div className="schedule-info">
            <div className="info-box warning">
              <h3>⚠️ Important</h3>
              <ul>
                <li>Ensure all critical backups are current</li>
                <li>Schedule during maintenance window</li>
                <li>Notify users of potential downtime</li>
                <li>Keep rollback plan accessible</li>
              </ul>
            </div>

            <div className="info-box info">
              <h3>ℹ️ What Happens</h3>
              <ol>
                <li>System will run pre-upgrade checks</li>
                <li>Automatic backups will be created</li>
                <li>Upgrade process will begin</li>
                <li>System will reboot as needed</li>
                <li>Post-upgrade verification will run</li>
              </ol>
            </div>
          </div>

          {queuedMessage ? (
            <div className="action-message" style={{ color: '#3a9d6e', borderColor: 'rgba(58, 157, 110, 0.3)' }}>
              ✓ {queuedMessage}
            </div>
          ) : (
            <>
              <label className="checkbox-option" style={{ marginTop: '16px' }}>
                <input
                  type="checkbox"
                  checked={confirmUpgrade}
                  onChange={(e) => setConfirmUpgrade(e.target.checked)}
                />
                <span>
                  I understand this changes the OS release repositories, runs a full distribution
                  upgrade, and reboots the server. A backup of /etc/apt and the package list is taken first.
                </span>
              </label>
              <div className="action-buttons">
                <button className="btn-cancel" onClick={() => setStep('analyze')} disabled={starting}>
                  Cancel
                </button>
                <button
                  className="btn-schedule-upgrade"
                  onClick={handleStartUpgrade}
                  disabled={!confirmUpgrade || starting}
                >
                  {starting ? 'Queuing…' : '▶ Start Upgrade'}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
