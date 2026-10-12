import { useState, useEffect } from 'react'
import './JobCreate.css'

interface Server {
  id: string
  hostname: string
  os_type: string
  os_version?: string
  connectivity_status: string
  port?: string
  credentials?: any
}

interface Patch {
  name: string
  version: string
  patch_type: string
  description?: string
  safety_level?: string
  risk_factors?: string[]
}

interface JobCreateProps {
  onJobCreated?: (jobId: string) => void
}

type Step = 1 | 2 | 3 | 4 | 5

export function JobCreate({ onJobCreated }: JobCreateProps) {
  const [currentStep, setCurrentStep] = useState<Step>(1)
  const [servers, setServers] = useState<Server[]>([])
  const [allPatches, setAllPatches] = useState<Record<string, Patch[]>>({})
  const [selectedServers, setSelectedServers] = useState<Set<string>>(new Set())
  const [selectedPatches, setSelectedPatches] = useState<Set<string>>(new Set())
  const [jobName, setJobName] = useState('')
  const [priority, setPriority] = useState('medium')
  const [dryRun, setDryRun] = useState(false)
  const [autoRollback, setAutoRollback] = useState(true)
  const [deploymentStrategy, setDeploymentStrategy] = useState('all')
  const [canaryPercent, setCanaryPercent] = useState('10')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [serverSearchQuery, setServerSearchQuery] = useState('')
  const [patchSearchQuery, setPatchSearchQuery] = useState('')
  const [criticalApps, setCriticalApps] = useState<Record<string, string[]>>({})
  const [rebootAfter, setRebootAfter] = useState(false)
  const [gracefulReboot, setGracefulReboot] = useState(true)
  const [rebootStatus, setRebootStatus] = useState<Record<string, boolean>>({})
  const [patchesRequiringReboot, setPatchesRequiringReboot] = useState<string[]>([])
  const [showDeployedPatches, setShowDeployedPatches] = useState(false)
  const [backupBefore, setBackupBefore] = useState(false)
  const [restoreOnFailure, setRestoreOnFailure] = useState(false)

  useEffect(() => {
    fetchServers()
  }, [])

  // Check reboot requirements whenever patches change
  useEffect(() => {
    if (currentStep >= 2) {
      checkRebootRequirements()
    }
  }, [selectedPatches])

  // Re-fetch patches when showDeployedPatches toggle changes
  useEffect(() => {
    if (selectedServers.size > 0) {
      fetchServers()
    }
  }, [showDeployedPatches])

  const fetchServers = async () => {
    try {
      const response = await fetch(`/api/v1/servers`)
      const data = await response.json()
      setServers(data)

      await Promise.all(data.map(async (server: Server) => {
        try {
          const patchRes = await fetch(`/api/v1/servers/${server.id}/patches?include_deployed=${showDeployedPatches}`)
          const patchData = await patchRes.json()
          setAllPatches(prev => ({ ...prev, [server.id]: patchData.patches || [] }))
        } catch (err) {
          console.error(`Failed to fetch patches for ${server.id}:`, err)
        }
      }))

      await Promise.all(data.map(async (server: Server) => {
        try {
          const appsRes = await fetch(`/api/v1/servers/${server.id}/critical-applications`)
          const appsData = await appsRes.json()
          setCriticalApps(prev => ({ ...prev, [server.id]: appsData.critical_services || [] }))
        } catch (err) {
          console.error(`Failed to fetch critical apps for ${server.id}:`, err)
        }
      }))
    } catch (err) {
      console.error('Failed to fetch servers:', err)
      setError('Failed to load servers')
    }
  }

  const [riskPrompt, setRiskPrompt] = useState<any>(null)

  const handleCreateJob = async (approved: boolean = false) => {
    setLoading(true)
    setError(null)

    try {
      // Convert patch keys to actual package names from descriptions
      const convertPatchesToPackages = () => {
        const packages: string[] = []
        selectedPatches.forEach(patchKey => {
          const patch = allAvailablePatches.find(p => `${p.name}-${p.version}` === patchKey)
          if (patch && patch.description) {
            // Extract packages from description (e.g., "Packages: curl, openssl, git")
            const packageStr = patch.description.replace('Packages: ', '')
            // Split by comma and filter out "+X more" text
            const patchPackages = packageStr.split(',')
              .map(p => p.trim())
              .filter(p => !p.includes('+') && p.length > 0)
            packages.push(...patchPackages)
          } else if (patch) {
            // Fallback to patch name if no description
            packages.push(patch.name)
          }
        })
        return packages
      }

      const response = await fetch(`/api/v1/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: jobName,
          server_ids: Array.from(selectedServers),
          patch_ids: convertPatchesToPackages(),
          priority,
          dry_run: dryRun,
          auto_rollback: autoRollback,
          reboot_after: rebootAfter,
          graceful_reboot: gracefulReboot,
          backup_before: backupBefore,
          restore_on_failure: backupBefore && restoreOnFailure,
          approved_risks: approved,
          deployment_strategy: deploymentStrategy,
          canary_percent: Number(canaryPercent) || 10,
        }),
      })

      const data = await response.json()
      if (response.status === 409 && data.detail && data.detail.servers) {
        setRiskPrompt(data.detail)
        return
      }
      if (!response.ok) {
        throw new Error(data.detail || 'Failed to create job')
      }

      setSuccess(`✓ Job created successfully! ID: ${data.id}`)
      setJobName('')
      setSelectedServers(new Set())
      setSelectedPatches(new Set())
      setCurrentStep(1)

      if (onJobCreated) {
        onJobCreated(data.id)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred')
    } finally {
      setLoading(false)
    }
  }

  const canProceed = (): boolean => {
    switch (currentStep) {
      case 1: return selectedServers.size > 0
      case 2: return selectedPatches.size > 0
      case 3: return true
      case 4: return true
      case 5: return jobName.trim().length > 0
      default: return false
    }
  }

  const filteredServers = servers.filter(s =>
    s.hostname.toLowerCase().includes(serverSearchQuery.toLowerCase())
  )

  const allAvailablePatches = Array.from(selectedServers)
    .flatMap(serverId => allPatches[serverId] || [])
    .filter((patch, idx, self) => idx === self.findIndex(p => p.name === patch.name))
    .filter(p => p.name.toLowerCase().includes(patchSearchQuery.toLowerCase()))

  const getSafetyColor = (level?: string): string => {
    if (!level) return '#8b95a5'
    if (level === 'safe') return '#3a9d6e'
    if (level === 'caution') return '#d4a132'
    if (level === 'danger') return '#c8514c'
    if (level === 'blocked') return '#c8514c'
    return '#8b95a5'
  }

  // Check which patches require reboot
  const checkRebootRequirements = () => {
    const rebootPkgs: string[] = []
    selectedPatches.forEach(patchKey => {
      const patch = allAvailablePatches.find(p => `${p.name}-${p.version}` === patchKey)
      if (patch) {
        const name = patch.name.toLowerCase()
        if (name.includes('kernel') || name.includes('linux-image') || name.includes('linux-headers') ||
            name.includes('grub') || name.includes('glibc') || name.includes('libc6') ||
            name.includes('systemd') || name.includes('openssh-server')) {
          rebootPkgs.push(patch.name)
        }
      }
    })
    setPatchesRequiringReboot(rebootPkgs)
    return rebootPkgs.length > 0
  }

  // Fetch reboot status for selected servers
  const fetchRebootStatus = async () => {
    const status: Record<string, boolean> = {}

    for (const serverId of Array.from(selectedServers)) {
      try {
        const res = await fetch(`/api/v1/servers/${serverId}/reboot-status`)
        const data = await res.json()
        status[serverId] = data.reboot_required || false
      } catch (err) {
        console.error(`Failed to check reboot status for ${serverId}:`, err)
      }
    }
    setRebootStatus(status)
  }

  return (
    <div className="job-create-wizard">
      {/* Progress Indicator */}
      <div className="wizard-progress">
        <div className="progress-steps">
          {[1, 2, 3, 4, 5].map((step) => (
            <button
              key={step}
              className={`step-indicator ${currentStep === step ? 'active' : ''} ${step < currentStep ? 'completed' : ''}`}
              onClick={() => step < currentStep && setCurrentStep(step as Step)}
            >
              {step < currentStep ? '✓' : step}
            </button>
          ))}
        </div>
        <div className="progress-labels">
          <span>Servers</span>
          <span>Patches</span>
          <span>Options</span>
          <span>Schedule</span>
          <span>Review</span>
        </div>
      </div>

      {/* Error/Success Messages */}
      {error && (
        <div className="alert alert-error">
          <span>✕ {error}</span>
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}
      {success && (
        <div className="alert alert-success">
          <span>{success}</span>
          <button onClick={() => setSuccess(null)}>✕</button>
        </div>
      )}

      {/* Step 1: Select Servers */}
      {currentStep === 1 && (
        <div className="wizard-step">
          <h2>Select Servers</h2>
          <p>Choose which servers to patch ({selectedServers.size} selected)</p>

          <input
            type="text"
            placeholder="Search servers by hostname..."
            value={serverSearchQuery}
            onChange={(e) => setServerSearchQuery(e.target.value)}
            className="search-box"
          />

          <div className="server-grid">
            {filteredServers.map(server => (
              <label key={server.id} className="server-checkbox-label">
                <input
                  type="checkbox"
                  checked={selectedServers.has(server.id)}
                  onChange={(e) => {
                    const updated = new Set(selectedServers)
                    if (e.target.checked) {
                      updated.add(server.id)
                    } else {
                      updated.delete(server.id)
                    }
                    setSelectedServers(updated)
                  }}
                />
                <span className="checkbox-content">
                  <span className="server-name">🖥️ {server.hostname}</span>
                  <span className="server-info">{server.os_type}</span>
                  <span className={`server-status status-${server.connectivity_status}`}>
                    {server.connectivity_status === 'healthy' ? '● Healthy' : '● Unreachable'}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Step 2: Select Patches */}
      {currentStep === 2 && (
        <div className="wizard-step">
          <h2>Select Patches</h2>
          <p>Choose which patches to deploy ({selectedPatches.size} selected)</p>

          {/* Critical applications warning */}
          {Array.from(selectedServers).length > 0 && (
            <div className="critical-apps-section">
              {Array.from(selectedServers).map(serverId => {
                const apps = criticalApps[serverId]
                if (!apps || apps.length === 0) return null
                const server = servers.find(s => s.id === serverId)
                return (
                  <div key={serverId} className="app-warning">
                    <span className="warning-icon">⚠️</span>
                    <div>
                      <strong>{server?.hostname}</strong> runs: {apps.join(', ')}
                      <div className="warning-text">
                        Critical applications detected. Patches will be checked for compatibility.
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          <div className="patch-controls">
            <input
              type="text"
              placeholder="Search patches by name..."
              value={patchSearchQuery}
              onChange={(e) => setPatchSearchQuery(e.target.value)}
              className="search-box"
            />
            <div className="select-buttons">
              <button
                className="btn-select-all"
                onClick={() => setSelectedPatches(new Set(allAvailablePatches.map(p => `${p.name}-${p.version}`)))}
              >
                Select All
              </button>
              <button
                className="btn-deselect-all"
                onClick={() => setSelectedPatches(new Set())}
              >
                Deselect All
              </button>
            </div>
          </div>

          <label className="show-deployed-toggle">
            <input
              type="checkbox"
              checked={showDeployedPatches}
              onChange={(e) => setShowDeployedPatches(e.target.checked)}
            />
            <span>Show already-deployed patches (for re-deployment testing)</span>
          </label>

          <div className="patch-grid">
            {allAvailablePatches.map((patch, idx) => {
              const patchKey = `${patch.name}-${patch.version}`
              const packageList = patch.description?.replace('Packages: ', '') || 'No packages info'
              return (
                <label key={idx} className="patch-checkbox-label" title={packageList}>
                  <input
                    type="checkbox"
                    checked={selectedPatches.has(patchKey)}
                    onChange={(e) => {
                      const updated = new Set(selectedPatches)
                      if (e.target.checked) {
                        updated.add(patchKey)
                      } else {
                        updated.delete(patchKey)
                      }
                      setSelectedPatches(updated)
                    }}
                  />
                  <span className="patch-content">
                    <span className="patch-name">📦 {patch.name}</span>
                    <span className="patch-version">{patch.version}</span>
                    <span className="patch-description">{packageList}</span>
                    {patch.safety_level && (
                      <span className="patch-safety" style={{ color: getSafetyColor(patch.safety_level) }}>
                        ● {patch.safety_level.toUpperCase()}
                      </span>
                    )}
                  </span>
                </label>
              )
            })}
          </div>
        </div>
      )}

      {/* Step 3: Options */}
      {currentStep === 3 && (
        <div className="wizard-step">
          <h2>Deployment Options</h2>

          <div className="options-group">
            <h3>Deployment Strategy</h3>
            <div className="radio-group">
              <label>
                <input
                  type="radio"
                  name="strategy"
                  value="all"
                  checked={deploymentStrategy === 'all'}
                  onChange={(e) => setDeploymentStrategy(e.target.value)}
                />
                <span>All servers at once (fastest)</span>
              </label>
              <label>
                <input
                  type="radio"
                  name="strategy"
                  value="canary"
                  checked={deploymentStrategy === 'canary'}
                  onChange={(e) => setDeploymentStrategy(e.target.value)}
                />
                <span>Canary deployment</span>
              </label>
              {deploymentStrategy === 'canary' && (
                <div className="sub-option">
                  <label>Canary %: <input type="number" min="1" max="50" value={canaryPercent} onChange={(e) => setCanaryPercent(e.target.value)} /></label>
                </div>
              )}
              <label>
                <input
                  type="radio"
                  name="strategy"
                  value="staged"
                  checked={deploymentStrategy === 'staged'}
                  onChange={(e) => setDeploymentStrategy(e.target.value)}
                />
                <span>Staged rollout (25% → 50% → 100%)</span>
              </label>
            </div>
          </div>

          <div className="options-group">
            <h3>Safety Options</h3>
            <label className="checkbox-option">
              <input
                type="checkbox"
                checked={dryRun}
                onChange={(e) => setDryRun(e.target.checked)}
              />
              <span>Dry Run (preview without applying)</span>
            </label>
            <label className="checkbox-option">
              <input
                type="checkbox"
                checked={autoRollback}
                onChange={(e) => setAutoRollback(e.target.checked)}
              />
              <span>Auto Rollback on Failure</span>
            </label>
            <label className="checkbox-option">
              <input
                type="checkbox"
                checked={backupBefore}
                onChange={(e) => {
                  setBackupBefore(e.target.checked)
                  if (!e.target.checked) setRestoreOnFailure(false)
                }}
              />
              <span>Back up each server before patching (requires a backup provider in Admin Settings)</span>
            </label>
            {backupBefore && (
              <label className="checkbox-option sub-option">
                <input
                  type="checkbox"
                  checked={restoreOnFailure}
                  onChange={(e) => setRestoreOnFailure(e.target.checked)}
                />
                <span>If rollback does not recover a server, restore its VM from the backup</span>
              </label>
            )}
          </div>

          <div className="options-group">
            <h3>Priority</h3>
            <select value={priority} onChange={(e) => setPriority(e.target.value)} className="priority-select">
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
          </div>

          <div className="options-group">
            <h3>Reboot Options</h3>
            {patchesRequiringReboot.length > 0 && (
              <div className="reboot-required-notice">
                ⚠️ Reboot required for: {patchesRequiringReboot.slice(0, 2).join(', ')}
                {patchesRequiringReboot.length > 2 && ` +${patchesRequiringReboot.length - 2} more`}
              </div>
            )}
            <label className="checkbox-option">
              <input
                type="checkbox"
                checked={rebootAfter}
                onChange={(e) => {
                  setRebootAfter(e.target.checked)
                  if (e.target.checked && selectedServers.size > 0) {
                    fetchRebootStatus()
                  }
                }}
              />
              <span>Reboot servers if patches require it</span>
            </label>
            {rebootAfter && (
              <>
                <label className="checkbox-option sub-option">
                  <input
                    type="checkbox"
                    checked={gracefulReboot}
                    onChange={(e) => setGracefulReboot(e.target.checked)}
                  />
                  <span>Graceful reboot (shutdown -r +1) - gives services time to stop</span>
                </label>
                {!gracefulReboot && (
                  <label className="checkbox-option sub-option">
                    <span style={{ color: '#c8514c' }}>Immediate reboot - services may not shut down cleanly</span>
                  </label>
                )}
              </>
            )}
            {Object.values(rebootStatus).some(v => v) && (
              <div className="reboot-pending-notice">
                ⚠️ Reboot already pending on some servers. Patching will proceed but affected servers need reboot.
              </div>
            )}
          </div>
        </div>
      )}

      {/* Step 4: Schedule */}
      {currentStep === 4 && (
        <div className="wizard-step">
          <h2>Schedule Deployment</h2>

          <div className="schedule-grid">
            <div className="schedule-card">
              <h3>Maintenance Windows</h3>
              <p>Next available: Today 02:00 UTC</p>
              <p>Estimated duration: {Math.ceil(selectedPatches.size * 2)} minutes</p>
              <label className="checkbox-option">
                <input type="checkbox" defaultChecked />
                <span>Deploy during maintenance window</span>
              </label>
            </div>

            <div className="schedule-card">
              <h3>Approvals Required</h3>
              <p>Current approval chain:</p>
              <ul>
                <li>✓ Admin approval (auto)</li>
                <li>{selectedPatches.size > 5 ? '⚠ Manager approval required' : '✓ Auto-approved'}</li>
                <li>{deploymentStrategy === 'all' ? '⚠ Director approval required' : '✓ Auto-approved'}</li>
              </ul>
            </div>
          </div>
        </div>
      )}

      {/* Step 5: Review */}
      {currentStep === 5 && (
        <div className="wizard-step">
          <h2>Review & Deploy</h2>

          <div className="review-summary">
            <div className="summary-section">
              <h3>Job Details</h3>
              <input
                type="text"
                placeholder="Enter job name (e.g., 'June Security Updates')"
                value={jobName}
                onChange={(e) => setJobName(e.target.value)}
                className="job-name-input"
              />
              <div className="summary-item">
                <span>Priority:</span>
                <strong>{priority.toUpperCase()}</strong>
              </div>
            </div>

            <div className="summary-section">
              <h3>Deployment Summary</h3>
              <div className="summary-item">
                <span>Servers:</span>
                <strong>{selectedServers.size} servers</strong>
              </div>
              <div className="summary-item">
                <span>Patches:</span>
                <strong>{selectedPatches.size} patches</strong>
              </div>
              {selectedPatches.size > 0 && (
                <div className="patches-list">
                  <div className="patches-label">Included patches:</div>
                  {allAvailablePatches.filter(p => selectedPatches.has(`${p.name}-${p.version}`)).map((patch, idx) => (
                    <div key={idx} className="patch-item">
                      <span className="patch-name">{patch.name}</span>
                      <span className="patch-version">{patch.version}</span>
                    </div>
                  ))}
                </div>
              )}
              <div className="summary-item">
                <span>Strategy:</span>
                <strong>{deploymentStrategy === 'all' ? 'All at once' : deploymentStrategy === 'canary' ? `Canary (${canaryPercent}%)` : 'Staged'}</strong>
              </div>
              <div className="summary-item">
                <span>Dry Run:</span>
                <strong>{dryRun ? 'Yes' : 'No (Apply patches)'}</strong>
              </div>
              <div className="summary-item">
                <span>Auto Rollback:</span>
                <strong>{autoRollback ? 'Enabled' : 'Disabled'}</strong>
              </div>
            </div>

            <div className="safety-checklist">
              <h3>✓ Safety Checklist</h3>
              <label className="checklist-item">
                <input type="checkbox" defaultChecked disabled />
                <span>Pre-patch health checks enabled</span>
              </label>
              <label className="checklist-item">
                <input type="checkbox" defaultChecked disabled />
                <span>Post-patch validation enabled</span>
              </label>
              <label className="checklist-item">
                <input type="checkbox" defaultChecked disabled />
                <span>Automatic rollback configured</span>
              </label>
            </div>
          </div>
        </div>
      )}

      {/* Navigation Buttons */}
      <div className="wizard-footer">
        <button
          className="btn btn-secondary"
          onClick={() => setCurrentStep((prev) => Math.max(1, prev - 1) as Step)}
          disabled={currentStep === 1}
        >
          ← Back
        </button>

        {currentStep < 5 ? (
          <button
            className="btn btn-primary"
            onClick={() => setCurrentStep((prev) => Math.min(5, prev + 1) as Step)}
            disabled={!canProceed()}
          >
            Next →
          </button>
        ) : (
          <button
            className="btn btn-deploy"
            onClick={() => handleCreateJob(false)}
            disabled={loading || !jobName.trim()}
          >
            {loading ? 'Creating Job...' : '🚀 Deploy'}
          </button>
        )}
      {riskPrompt && (
        <div className="add-server-modal-overlay" onClick={() => setRiskPrompt(null)}>
          <div className="add-server-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 720 }}>
            <h3>⚠️ Review before creating this job</h3>
            <p style={{ color: '#d5dbe3' }}>{riskPrompt.message}</p>

            {riskPrompt.kubernetes_order && riskPrompt.kubernetes_order.length > 0 && (
              <div className="reboot-required-notice">
                Kubernetes nodes will be patched one at a time, in this order:{' '}
                <strong>{riskPrompt.kubernetes_order.join(' → ')}</strong>.
                Each node is drained, patched, rebooted if needed, and must show kubelet and containerd running
                and Ready before the next node starts. If a node fails, the remaining nodes are skipped.
              </div>
            )}

            <div style={{ maxHeight: 360, overflowY: 'auto' }}>
              {riskPrompt.servers.map((srv: any) => (
                <div key={srv.server_id} style={{ borderBottom: '1px solid rgba(212, 161, 50, 0.15)', padding: '8px 0' }}>
                  <strong>{srv.hostname}</strong>
                  {!srv.ok && <div style={{ color: '#c8514c' }}>Analysis failed: {srv.error}</div>}
                  {srv.unresolved && srv.unresolved.length > 0 && (
                    <div style={{ color: '#c8514c' }}>Cannot resolve on this server: {srv.unresolved.join(', ')}</div>
                  )}
                  {(srv.warnings || []).map((w: string, i: number) => (
                    <div key={i} style={{ color: '#d4a132', fontSize: '0.9em' }}>• {w}</div>
                  ))}
                  {srv.ok && (!srv.warnings || srv.warnings.length === 0) && (
                    <div style={{ color: '#3a9d6e', fontSize: '0.9em' }}>No running applications affected</div>
                  )}
                </div>
              ))}
            </div>

            <div className="form-actions">
              <button className="btn-cancel" onClick={() => setRiskPrompt(null)}>Cancel job</button>
              <button
                className="btn-save"
                onClick={() => {
                  setRiskPrompt(null)
                  handleCreateJob(true)
                }}
              >
                Approve and create job
              </button>
            </div>
          </div>
        </div>
      )}
      </div>
    </div>
  )
}
