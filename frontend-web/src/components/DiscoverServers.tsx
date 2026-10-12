import { useEffect, useState } from 'react'
import './ServerManagement.css'

interface Added {
  ip: string
  hostname: string
  os_type: string
  os_version?: string
}

interface Skipped {
  ip: string
  reason: string
  status?: string
  host_key_fingerprint?: string | null
}

interface DiscoveryResult {
  total: number
  added: Added[]
  skipped: Skipped[]
}

interface DiscoverServersProps {
  onComplete: () => void
}

export function DiscoverServers({ onComplete }: DiscoverServersProps) {
  const [open, setOpen] = useState(false)
  const [targets, setTargets] = useState('')
  const [port, setPort] = useState('22')
  const [username, setUsername] = useState('')
  const [authMethod, setAuthMethod] = useState<'ssh_password' | 'ssh_key'>('ssh_password')
  const [password, setPassword] = useState('')
  const [privateKey, setPrivateKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [taskState, setTaskState] = useState<string | null>(null)
  const [result, setResult] = useState<DiscoveryResult | null>(null)

  const apiBase = `/api/v1/servers`

  useEffect(() => {
    if (!taskId) return
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${apiBase}/discover/${taskId}`)
        const data = await res.json()
        setTaskState(data.state)
        if (data.state === 'SUCCESS') {
          setResult(data.result)
          setTaskId(null)
          onComplete()
        } else if (data.state === 'FAILURE') {
          setError(data.error || 'Discovery failed')
          setTaskId(null)
        }
      } catch (err) {
        setError('Lost contact with the backend while scanning')
        setTaskId(null)
      }
    }, 2000)
    return () => clearInterval(timer)
  }, [taskId])

  const reset = () => {
    setTargets('')
    setPassword('')
    setPrivateKey('')
    setError(null)
    setResult(null)
    setTaskState(null)
  }

  const close = () => {
    setOpen(false)
    reset()
  }

  // Hosts whose key was read but not yet approved: the administrator approves each fingerprint and scans again
  const [approvals, setApprovals] = useState<Record<string, string>>({})

  const startScan = async (approved: Record<string, string> = {}) => {
    setError(null)
    setResult(null)
    if (!targets.trim()) {
      setError('Enter a subnet (10.0.0.0/24), a range (10.0.0.10-50) or addresses')
      return
    }
    setStarting(true)
    try {
      const res = await fetch(`${apiBase}/discover`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          targets,
          port: Number(port) || 22,
          username,
          auth_method: authMethod,
          password: authMethod === 'ssh_password' ? password : undefined,
          private_key: authMethod === 'ssh_key' ? privateKey : undefined,
          approved_fingerprints: approved,
        }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail || 'Failed to start discovery')
        return
      }
      setTaskState('PENDING')
      setTaskId(data.task_id)
    } catch (err) {
      setError('Failed to start discovery')
    } finally {
      setStarting(false)
    }
  }

  const scanning = taskId !== null

  return (
    <>
      <button className="btn-add-server" onClick={() => setOpen(true)}>🔎 Discover Servers</button>

      {open && (
        <div className="add-server-modal-overlay" onClick={() => !scanning && close()}>
          <div className="add-server-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Discover Linux Servers</h3>

            {!result && !scanning && (
              <>
                <p style={{ color: '#8b95a5', fontSize: '0.9em' }}>
                  Every address is checked. Only hosts that accept SSH with these credentials and report
                  a supported Linux distribution are added. Windows hosts, network appliances and
                  anything else are skipped.
                </p>
                {error && <div className="error-message">{error}</div>}
                <div className="form-group">
                  <label>Targets</label>
                  <input
                    type="text"
                    className="form-input"
                    placeholder="192.0.2.0/24 or 192.0.2.10-50"
                    value={targets}
                    onChange={(e) => setTargets(e.target.value)}
                  />
                  <small>Subnet (CIDR), last-octet range, full range, or comma-separated addresses. Max 1024.</small>
                </div>
                <div className="form-group">
                  <label>SSH port</label>
                  <input type="text" className="form-input" value={port} onChange={(e) => setPort(e.target.value)} />
                </div>
                <div className="form-group">
                  <label>Username</label>
                  <input type="text" className="form-input" value={username} onChange={(e) => setUsername(e.target.value)} />
                </div>
                <div className="form-group">
                  <label>Authentication</label>
                  <select className="form-input" value={authMethod}
                          onChange={(e) => setAuthMethod(e.target.value as 'ssh_password' | 'ssh_key')}>
                    <option value="ssh_password">Password</option>
                    <option value="ssh_key">Private key</option>
                  </select>
                </div>
                {authMethod === 'ssh_password' ? (
                  <div className="form-group">
                    <label>Password</label>
                    <input type="password" className="form-input" value={password} onChange={(e) => setPassword(e.target.value)} />
                  </div>
                ) : (
                  <div className="form-group">
                    <label>Private key</label>
                    <textarea className="form-input" rows={5} value={privateKey} onChange={(e) => setPrivateKey(e.target.value)} />
                  </div>
                )}
                <div className="form-actions">
                  <button className="btn-cancel" onClick={close}>Cancel</button>
                  <button className="btn-save" onClick={() => startScan()} disabled={starting}>
                    {starting ? 'Starting…' : 'Start Discovery'}
                  </button>
                </div>
              </>
            )}

            {scanning && (
              <div style={{ color: '#8b95a5' }}>
                <p>Scanning… ({taskState === 'STARTED' ? 'in progress' : 'queued'})</p>
                <p style={{ fontSize: '0.85em' }}>Large subnets take a few minutes. You can keep working; this window
                  updates when the scan finishes.</p>
              </div>
            )}

            {result && (
              <>
                <p style={{ color: '#e6ebf1' }}>
                  Checked {result.total} addresses: <strong style={{ color: '#3a9d6e' }}>{result.added.length} added</strong>,
                  {' '}{result.skipped.length} skipped.
                </p>
                {result.added.length > 0 && (
                  <table className="discover-table">
                    <thead><tr><th>IP</th><th>Hostname</th><th>OS</th></tr></thead>
                    <tbody>
                      {result.added.map(a => (
                        <tr key={a.ip}>
                          <td><code>{a.ip}</code></td>
                          <td>{a.hostname}</td>
                          <td>{a.os_type} {a.os_version || ''}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {result.skipped.length > 0 && (
                  <details>
                    <summary style={{ color: '#8b95a5', cursor: 'pointer' }}>Skipped ({result.skipped.length})</summary>
                    <table className="discover-table">
                      <thead><tr><th>IP</th><th>Reason</th><th></th></tr></thead>
                      <tbody>
                        {result.skipped.map(s => (
                          <tr key={s.ip}>
                            <td><code>{s.ip}</code></td>
                            <td>{s.reason}</td>
                            <td>
                              {s.status === 'host_key_unapproved' && s.host_key_fingerprint && (
                                <label className="settings-toggle">
                                  <input
                                    type="checkbox"
                                    checked={approvals[s.ip] === s.host_key_fingerprint}
                                    onChange={(e) => {
                                      const next = { ...approvals }
                                      if (e.target.checked) next[s.ip] = s.host_key_fingerprint as string
                                      else delete next[s.ip]
                                      setApprovals(next)
                                    }}
                                  />
                                  Approve key
                                </label>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {Object.keys(approvals).length > 0 && (
                      <p className="settings-help">
                        Only approve a key after comparing its fingerprint with the one shown by the server
                        (ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub).
                        <button className="btn-save" style={{ marginLeft: 8 }} onClick={() => startScan(approvals)}>
                          Scan with {Object.keys(approvals).length} approved key(s)
                        </button>
                      </p>
                    )}
                  </details>
                )}
                <div className="form-actions">
                  <button className="btn-cancel" onClick={reset}>Scan again</button>
                  <button className="btn-save" onClick={close}>Done</button>
                </div>
              </>
            )}

          </div>
        </div>
      )}
    </>
  )
}
