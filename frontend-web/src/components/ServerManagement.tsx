import { useState, useEffect } from 'react'
import './ServerManagement.css'
import { Pagination } from './Pagination'
import { DiscoverServers } from './DiscoverServers'
import { ServerBackups } from './ServerBackups'

interface Server {
  id: string
  hostname: string
  ip_address?: string
  os_type: string
  os_version?: string
  auth_method: string
  connectivity_status: string
  created_at: string
  port?: string
  credentials?: any
  pending_patch_count?: number | null
  pending_scan_error?: string | null
  health?: { cpu_pct: number | null; memory_pct: number | null; disk_pct: number | null; load_1m: number; cpu_cores: number; uptime_seconds: number; collected_at: string; error?: string } | null
  last_patch_scan_at?: string | null
}

interface ServerStats {
  totalServers: number
  healthyServers: number
  unhealthyServers: number
  pendingPatches: number
  lastPatchDate?: string
}

export function ServerManagement({ onOpenServer }: { onOpenServer?: (id: string) => void } = {}) {
  const [servers, setServers] = useState<Server[]>([])
  const [filteredServers, setFilteredServers] = useState<Server[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedServerId, setExpandedServerId] = useState<string | null>(null)
  const [stats, setStats] = useState<ServerStats>({
    totalServers: 0,
    healthyServers: 0,
    unhealthyServers: 0,
    pendingPatches: 0,
  })

  // Filters
  const [filters, setFilters] = useState({
    osType: 'all',
    healthStatus: 'all',
  })

  const [currentPage, setCurrentPage] = useState(1)
  const defaultPageSize = useDefaultPageSize()
  const [pageSizeOverride, setPageSize] = useState<number | null>(null)
  const pageSize = pageSizeOverride ?? defaultPageSize
  const [showAddForm, setShowAddForm] = useState(false)
  const [newServer, setNewServer] = useState({
    hostname: '',
    ip_address: '',
    os_type: 'ubuntu',
    os_version: '', // Will be auto-detected on connection test
    auth_method: 'ssh_key',
    port: '22',
    username: '',
    password: '',
    private_key: '',
  })
  const [addingServer, setAddingServer] = useState(false)
  const [addError, setAddError] = useState<string | null>(null)
  const [testingConnection, setTestingConnection] = useState(false)
  const [connectionStatus, setConnectionStatus] = useState<{success: boolean; message: string} | null>(null)
  const [editingServer, setEditingServer] = useState<Server | null>(null)
  const [editError, setEditError] = useState<string | null>(null)
  const [deletingServerId, setDeletingServerId] = useState<string | null>(null)
  const [testingConnectionId, setTestingConnectionId] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<{serverId: string; success: boolean; message: string} | null>(null)
  const [scanningServerId, setScanningServerId] = useState<string | null>(null)
  const [scanResult, setScanResult] = useState<{serverId: string; patches: any[]; count: number; refreshed: boolean | null} | null>(null)
  const [detectingVersion, setDetectingVersion] = useState(false)
  const [detectionResult, setDetectionResult] = useState<{success: boolean; version?: string; message: string} | null>(null)

  useEffect(() => {
    fetchServers()
  }, [])

  useEffect(() => {
    applyFilters()
  }, [servers, filters, searchQuery])

  const fetchServers = async () => {
    try {
      const response = await fetch(`/api/v1/servers`)
      const data = await response.json()
      setServers(data)

      // Calculate stats
      const healthyCount = data.filter((s: any) => s.connectivity_status === 'healthy').length
      const unhealthyCount = data.filter((s: any) => s.connectivity_status !== 'healthy').length

      setStats({
        totalServers: data.length,
        healthyServers: healthyCount,
        unhealthyServers: unhealthyCount,
        pendingPatches: data.reduce((sum: number, s: any) => sum + (s.pending_patch_count ?? 0), 0),
      })
    } catch (err) {
      console.error('Failed to fetch servers:', err)
    } finally {
      setLoading(false)
    }
  }

  const applyFilters = () => {
    let filtered = [...servers]

    // Apply search
    if (searchQuery) {
      filtered = filtered.filter(s =>
        s.hostname.toLowerCase().includes(searchQuery.toLowerCase()) ||
        s.ip_address?.toLowerCase().includes(searchQuery.toLowerCase())
      )
    }

    // Apply OS filter
    if (filters.osType !== 'all') {
      filtered = filtered.filter(s => s.os_type === filters.osType)
    }

    // Apply health status filter
    if (filters.healthStatus !== 'all') {
      filtered = filtered.filter(s => s.connectivity_status === filters.healthStatus)
    }

    setFilteredServers(filtered)
    setCurrentPage(1)
  }

  const getOsIcon = (osType: string): string => {
    if (osType.includes('ubuntu')) return '🐧'
    if (osType.includes('debian')) return '🐧'
    if (osType.includes('centos')) return '🐧'
    if (osType.includes('rhel')) return '🐧'
    if (osType.includes('fedora')) return '🐧'
    return '🖥️'
  }

  const getHealthColor = (status: string): string => {
    if (status === 'healthy') return '#3a9d6e'
    if (status === 'warning') return '#d4a132'
    return '#c8514c'
  }

  const getHealthLabel = (status: string): string => {
    return status.charAt(0).toUpperCase() + status.slice(1)
  }

  const paginatedServers = filteredServers.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  )

  const handleTestConnection = async () => {
    if (!newServer.hostname || !newServer.ip_address) {
      setConnectionStatus({ success: false, message: 'Hostname and IP address are required' })
      return
    }

    if (!newServer.username) {
      setConnectionStatus({ success: false, message: 'Username is required' })
      return
    }

    if (newServer.auth_method === 'ssh_password' && !newServer.password) {
      setConnectionStatus({ success: false, message: 'Password is required for SSH password authentication' })
      return
    }

    setTestingConnection(true)
    setConnectionStatus(null)
    setDetectingVersion(true)
    setDetectionResult(null)

    try {

      // Create server with auto-detection only when needed
      const createRes = await fetch(`/api/v1/servers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          hostname: newServer.hostname,
          ip_address: newServer.ip_address,
          os_type: newServer.os_type,
          os_version: '', // Empty - backend will auto-detect
          auth_method: newServer.auth_method,
          port: newServer.port,
          username: newServer.username,
          password: newServer.auth_method === 'ssh_password' ? newServer.password : undefined,
          private_key: newServer.auth_method === 'ssh_key' ? newServer.private_key : undefined,
        }),
      })

      if (!createRes.ok) {
        const data = await createRes.json()
        throw new Error(data.detail || 'Failed to test connection')
      }

      const serverData = await createRes.json()

      // Update with detected version
      setNewServer({ ...newServer, os_version: serverData.os_version || '' })
      setConnectionStatus({ success: true, message: 'Connection successful' })

      if (serverData.os_version) {
        setDetectionResult({
          success: true,
          version: serverData.os_version,
          message: `✓ Version auto-detected: ${serverData.os_version}`
        })
      } else {
        setDetectionResult({
          success: false,
          message: 'Connection ok but could not detect version'
        })
      }
    } catch (err) {
      setConnectionStatus({
        success: false,
        message: err instanceof Error ? err.message : 'Failed to test connection'
      })
      setDetectionResult({
        success: false,
        message: ''
      })
    } finally {
      setTestingConnection(false)
      setDetectingVersion(false)
    }
  }

  const handleDetectVersionExisting = async (serverId: string) => {
    try {
      const res = await fetch(`/api/v1/servers/${serverId}/detect-version`, {
        method: 'POST',
      })

      if (res.ok) {
        const result = await res.json()
        // Refresh server list
        fetchServers()
        setDetectionResult({
          success: true,
          version: result.os_version,
          message: `Updated to: ${result.os_version}`
        })
      } else {
        const data = await res.json()
        setDetectionResult({
          success: false,
          message: data.detail || 'Failed to detect version'
        })
      }
    } catch (err) {
      setDetectionResult({
        success: false,
        message: err instanceof Error ? err.message : 'Failed to detect version'
      })
    }
  }

  const handleDetectVersionInEdit = async () => {
    if (!editingServer) return

    setDetectingVersion(true)
    setDetectionResult(null)
    try {
      const res = await fetch(`/api/v1/servers/${editingServer.id}/detect-version`, {
        method: 'POST',
      })

      if (res.ok) {
        const result = await res.json()
        // Update the editing server with detected version
        setEditingServer({ ...editingServer, os_version: result.os_version })
        setDetectionResult({
          success: true,
          version: result.os_version,
          message: `Auto-detected: ${result.os_version}`
        })
      } else {
        const data = await res.json()
        setDetectionResult({
          success: false,
          message: data.detail || 'Failed to detect version'
        })
      }
    } catch (err) {
      setDetectionResult({
        success: false,
        message: err instanceof Error ? err.message : 'Failed to detect version'
      })
    } finally {
      setDetectingVersion(false)
    }
  }

  const handleAddServer = async () => {
    if (!newServer.hostname || !newServer.ip_address) {
      setAddError('Hostname and IP address are required')
      return
    }

    setAddingServer(true)
    setAddError(null)
    try {
      const response = await fetch(`/api/v1/servers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          hostname: newServer.hostname,
          ip_address: newServer.ip_address,
          os_type: newServer.os_type,
          os_version: newServer.os_version,
          auth_method: newServer.auth_method,
          port: newServer.port,
          username: newServer.username,
          password: newServer.auth_method === 'ssh_password' ? newServer.password : undefined,
          private_key: newServer.auth_method === 'ssh_key' ? newServer.private_key : undefined,
        }),
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'Failed to add server')
      }

      setShowAddForm(false)
      setNewServer({
        hostname: '',
        ip_address: '',
        os_type: 'ubuntu',
        os_version: '', // Auto-detected on test connection
        auth_method: 'ssh_key',
        port: '22',
        username: '',
        password: '',
        private_key: '',
      })
      setConnectionStatus(null)
      setDetectionResult(null)
      fetchServers()
    } catch (err) {
      setAddError(err instanceof Error ? err.message : 'Failed to add server')
    } finally {
      setAddingServer(false)
    }
  }

  const handleEditServer = (server: Server) => {
    setEditingServer(server)
    setEditError(null)
  }

  const handleSaveEdit = async () => {
    if (!editingServer) return

    try {
      const response = await fetch(`/api/v1/servers/${editingServer.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          hostname: editingServer.hostname,
          ip_address: editingServer.ip_address,
          os_version: editingServer.os_version,
          port: editingServer.port,
          username: editingServer.credentials?.username,
          password: editingServer.auth_method === 'ssh_password' ? editingServer.credentials?.password : undefined,
          private_key: editingServer.auth_method === 'ssh_key' ? editingServer.credentials?.private_key : undefined,
        }),
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'Failed to update server')
      }

      setEditingServer(null)
      setTestResult(null)
      fetchServers()
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Failed to update server')
    }
  }

  const handleDeleteServer = async (serverId: string) => {
    if (!window.confirm('Are you sure you want to delete this server?')) return

    setDeletingServerId(serverId)
    try {
      const response = await fetch(`/api/v1/servers/${serverId}`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'Failed to delete server')
      }

      fetchServers()
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to delete server')
    } finally {
      setDeletingServerId(null)
    }
  }

  const handleTestExistingConnection = async (serverId: string) => {
    setTestingConnectionId(serverId)
    setTestResult(null)
    try {
      const response = await fetch(`/api/v1/servers/${serverId}/test-connection`, {
        method: 'POST',
      })

      if (response.ok) {
        const result = await response.json()
        setTestResult({
          serverId,
          success: result.status === 'success',
          message: result.message,
        })
        fetchServers()
      } else {
        throw new Error('Connection test failed')
      }
    } catch (err) {
      setTestResult({
        serverId,
        success: false,
        message: err instanceof Error ? err.message : 'Connection test failed',
      })
    } finally {
      setTestingConnectionId(null)
    }
  }

  const handleScanPatches = async (serverId: string) => {
    setScanningServerId(serverId)
    setScanResult(null)
    try {
      const response = await fetch(`/api/v1/servers/${serverId}/patches?refresh=true`, {
        method: 'GET',
      })

      if (response.ok) {
        const result = await response.json()
        setScanResult({
          serverId,
          patches: result.patches || [],
          count: result.upgradable_packages ?? result.new_patches ?? 0,
          refreshed: result.index_refreshed ?? null,
        })
        fetchServers()
      } else {
        const data = await response.json()
        throw new Error(data.detail || 'Patch scan failed')
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to scan patches')
    } finally {
      setScanningServerId(null)
    }
  }

  if (loading) {
    return <div className="server-loading">Loading servers...</div>
  }

  const osTypes = [...new Set(servers.map(s => s.os_type))]

  return (
    <div className="server-management">
      {/* Statistics Bar */}
      <div className="server-stats-bar">
        <div className="stat-item">
          <div className="stat-label">Total Servers</div>
          <div className="stat-value">{stats.totalServers}</div>
        </div>
        <div className="stat-item healthy">
          <div className="stat-label">Healthy</div>
          <div className="stat-value">{stats.healthyServers}</div>
        </div>
        <div className="stat-item unhealthy">
          <div className="stat-label">Unhealthy</div>
          <div className="stat-value">{stats.unhealthyServers}</div>
        </div>
        <div className="stat-item">
          <div className="stat-label">Pending Patches</div>
          <div className="stat-value">{stats.pendingPatches}</div>
        </div>
      </div>

      <div className="server-container">
        {/* Filter Panel */}
        <aside className="filter-panel">
          <h3>Filters</h3>

          {/* Search */}
          <div className="filter-section">
            <label>Search</label>
            <input
              type="text"
              placeholder="Hostname or IP..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="search-input"
            />
          </div>

          {/* OS Type Filter */}
          <div className="filter-section">
            <label>Operating System</label>
            <select
              value={filters.osType}
              onChange={(e) => setFilters({ ...filters, osType: e.target.value })}
              className="filter-select"
            >
              <option value="all">All OS Types</option>
              {osTypes.map(os => (
                <option key={os} value={os}>{os}</option>
              ))}
            </select>
          </div>

          {/* Health Status Filter */}
          <div className="filter-section">
            <label>Health Status</label>
            <select
              value={filters.healthStatus}
              onChange={(e) => setFilters({ ...filters, healthStatus: e.target.value })}
              className="filter-select"
            >
              <option value="all">All Status</option>
              <option value="healthy">Healthy</option>
              <option value="warning">Warning</option>
              <option value="unreachable">Unreachable</option>
            </select>
          </div>

          {/* Active Filters */}
          <div className="active-filters">
            <span className="filter-count">
              Showing {filteredServers.length} of {servers.length} servers
            </span>
          </div>
        </aside>

        {/* Server Table */}
        <main className="servers-section">
          <div className="servers-header">
            <h2>Servers ({stats.totalServers})</h2>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button
                className="btn-add-server"
                onClick={async () => {
                  await fetch(`/api/v1/servers/scan-all`, { method: 'POST' })
                  alert('Scan queued for all servers. Counts update as each scan finishes; refresh in a few minutes.')
                }}
              >
                ↻ Scan all
              </button>
              <DiscoverServers onComplete={fetchServers} />
              <button className="btn-add-server" onClick={() => setShowAddForm(true)}>+ Add Server</button>
            </div>
          </div>

          {showAddForm && (
            <div className="add-server-modal-overlay" onClick={() => setShowAddForm(false)}>
              <div className="add-server-modal" onClick={(e) => e.stopPropagation()}>
                <h3>Add New Server</h3>
                {addError && <div className="error-message">{addError}</div>}
                <div className="form-grid">
                  <div className="form-group">
                    <label>Hostname *</label>
                    <input
                      type="text"
                      placeholder="e.g., web01"
                      value={newServer.hostname}
                      onChange={(e) => setNewServer({...newServer, hostname: e.target.value})}
                      className="form-input"
                    />
                  </div>
                  <div className="form-group">
                    <label>IP Address *</label>
                    <input
                      type="text"
                      placeholder="e.g., 192.0.2.101"
                      value={newServer.ip_address}
                      onChange={(e) => setNewServer({...newServer, ip_address: e.target.value})}
                      className="form-input"
                    />
                  </div>
                  <div className="form-group">
                    <label>OS Type</label>
                    <select
                      value={newServer.os_type}
                      onChange={(e) => setNewServer({...newServer, os_type: e.target.value})}
                      className="form-input"
                    >
                      <option value="ubuntu">Ubuntu</option>
                      <option value="debian">Debian</option>
                      <option value="centos">CentOS</option>
                      <option value="redhat">Red Hat</option>
                      <option value="fedora">Fedora</option>
                    </select>
                  </div>
                  {newServer.os_version && (
                    <div className="form-group">
                      <label>OS Version (auto-detected)</label>
                      <div style={{
                        padding: '10px 12px',
                        background: 'rgba(58, 157, 110, 0.1)',
                        border: '1px solid rgba(58, 157, 110, 0.3)',
                        color: '#3a9d6e',
                        borderRadius: '4px',
                        fontWeight: '600',
                      }}>
                        ✓ {newServer.os_version}
                      </div>
                    </div>
                  )}
                  <div className="form-group">
                    <label>Auth Method</label>
                    <select
                      value={newServer.auth_method}
                      onChange={(e) => setNewServer({...newServer, auth_method: e.target.value})}
                      className="form-input"
                    >
                      <option value="ssh_key">SSH Key</option>
                      <option value="ssh_password">SSH Password</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label>Port</label>
                    <input
                      type="text"
                      placeholder="22"
                      value={newServer.port}
                      onChange={(e) => setNewServer({...newServer, port: e.target.value})}
                      className="form-input"
                    />
                  </div>
                  <div className="form-group">
                    <label>Username</label>
                    <input
                      type="text"
                      placeholder="e.g., admin"
                      value={newServer.username}
                      onChange={(e) => setNewServer({...newServer, username: e.target.value})}
                      className="form-input"
                    />
                  </div>
                  {newServer.auth_method === 'ssh_password' && (
                    <div className="form-group">
                      <label>Password</label>
                      <input
                        type="password"
                        placeholder="SSH password"
                        value={newServer.password}
                        onChange={(e) => setNewServer({...newServer, password: e.target.value})}
                        className="form-input"
                      />
                    </div>
                  )}
                  {newServer.auth_method === 'ssh_key' && (
                    <div className="form-group">
                      <label>Private Key (optional)</label>
                      <textarea
                        placeholder="Paste private key content or leave empty for default key"
                        value={newServer.private_key}
                        onChange={(e) => setNewServer({...newServer, private_key: e.target.value})}
                        className="form-input"
                        rows={3}
                      />
                    </div>
                  )}
                </div>

                {connectionStatus && (
                  <div className={`connection-status ${connectionStatus.success ? 'success' : 'failed'}`}>
                    <span>{connectionStatus.success ? '✓' : '✕'} {connectionStatus.message}</span>
                  </div>
                )}

                <div className="form-actions">
                  <button className="btn-cancel" onClick={() => setShowAddForm(false)}>Cancel</button>
                  <button className="btn-test" onClick={handleTestConnection} disabled={testingConnection || !newServer.hostname || !newServer.ip_address}>
                    {testingConnection ? 'Testing...' : '🔌 Test Connection'}
                  </button>
                  <button className="btn-submit" onClick={handleAddServer} disabled={addingServer}>
                    {addingServer ? 'Adding...' : 'Add Server'}
                  </button>
                </div>
              </div>
            </div>
          )}

          {editingServer && (
            <div className="add-server-modal-overlay" onClick={() => setEditingServer(null)}>
              <div className="add-server-modal" onClick={(e) => e.stopPropagation()}>
                <h3>Edit Server: {editingServer.hostname}</h3>
                {editError && <div className="error-message">{editError}</div>}
                <div className="form-grid">
                  <div className="form-group">
                    <label>Hostname</label>
                    <input
                      type="text"
                      value={editingServer.hostname}
                      onChange={(e) => setEditingServer({...editingServer, hostname: e.target.value})}
                      className="form-input"
                    />
                  </div>
                  <div className="form-group">
                    <label>IP Address</label>
                    <input
                      type="text"
                      value={editingServer.ip_address || ''}
                      onChange={(e) => setEditingServer({...editingServer, ip_address: e.target.value})}
                      className="form-input"
                    />
                  </div>
                  <div className="form-group">
                    <label>OS Type</label>
                    <input
                      type="text"
                      value={editingServer.os_type}
                      disabled
                      className="form-input"
                    />
                  </div>
                  <div className="form-group">
                    <label>OS Version (auto-detected)</label>
                    <div style={{display: 'flex', gap: '8px', alignItems: 'center'}}>
                      <div style={{
                        flex: 1,
                        padding: '10px 12px',
                        background: 'rgba(58, 157, 110, 0.1)',
                        border: '1px solid rgba(58, 157, 110, 0.3)',
                        color: '#3a9d6e',
                        borderRadius: '4px',
                        fontWeight: '600',
                      }}>
                        ✓ {editingServer.os_version || 'Unknown'}
                      </div>
                      <button
                        type="button"
                        onClick={handleDetectVersionInEdit}
                        disabled={detectingVersion}
                        title="Re-detect OS version from server"
                        style={{
                          padding: '8px 16px',
                          background: detectingVersion ? '#8b95a5' : '#d4a132',
                          color: '#0f1319',
                          border: 'none',
                          borderRadius: '4px',
                          cursor: detectingVersion ? 'not-allowed' : 'pointer',
                          fontWeight: '600',
                          fontSize: '0.9em',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {detectingVersion ? 'Detecting...' : 'Re-detect'}
                      </button>
                    </div>
                    {detectionResult && (
                      <div style={{
                        marginTop: '8px',
                        padding: '8px',
                        borderRadius: '4px',
                        fontSize: '0.85em',
                        background: detectionResult.success ? 'rgba(58, 157, 110, 0.15)' : 'rgba(200, 81, 76, 0.15)',
                        color: detectionResult.success ? '#3a9d6e' : '#c8514c',
                      }}>
                        {detectionResult.message}
                      </div>
                    )}
                  </div>
                  <div className="form-group">
                    <label>Port</label>
                    <input
                      type="text"
                      value={editingServer.port || '22'}
                      onChange={(e) => setEditingServer({...editingServer, port: e.target.value})}
                      className="form-input"
                    />
                  </div>
                  <div className="form-group">
                    <label>Auth Method</label>
                    <select
                      value={editingServer.auth_method || 'ssh_key'}
                      onChange={(e) => setEditingServer({...editingServer, auth_method: e.target.value})}
                      className="form-input"
                    >
                      <option value="ssh_key">SSH Key</option>
                      <option value="ssh_password">SSH Password</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label>Username</label>
                    <input
                      type="text"
                      placeholder="e.g., admin"
                      value={editingServer.credentials?.username || ''}
                      onChange={(e) => setEditingServer({
                        ...editingServer,
                        credentials: {...(editingServer.credentials || {}), username: e.target.value}
                      })}
                      className="form-input"
                    />
                  </div>
                  {editingServer.auth_method === 'ssh_password' && (
                    <div className="form-group">
                      <label>Password</label>
                      <input
                        type="password"
                        placeholder="SSH password"
                        value={editingServer.credentials?.password || ''}
                        onChange={(e) => setEditingServer({
                          ...editingServer,
                          credentials: {...(editingServer.credentials || {}), password: e.target.value}
                        })}
                        className="form-input"
                      />
                    </div>
                  )}
                  {editingServer.auth_method === 'ssh_key' && (
                    <div className="form-group">
                      <label>Private Key (optional)</label>
                      <textarea
                        placeholder="Paste private key content or leave empty for default key"
                        value={editingServer.credentials?.private_key || ''}
                        onChange={(e) => setEditingServer({
                          ...editingServer,
                          credentials: {...(editingServer.credentials || {}), private_key: e.target.value}
                        })}
                        className="form-input"
                        rows={3}
                      />
                    </div>
                  )}
                </div>
                <div className="form-actions">
                  <button className="btn-cancel" onClick={() => setEditingServer(null)}>Cancel</button>
                  <button
                    className="btn-test"
                    onClick={() => handleTestExistingConnection(editingServer.id)}
                    disabled={testingConnectionId === editingServer.id}
                  >
                    {testingConnectionId === editingServer.id ? '🔌 Testing...' : '🔌 Test Connection'}
                  </button>
                  <button className="btn-submit" onClick={handleSaveEdit}>Save Changes</button>
                </div>
                {testResult && testResult.serverId === editingServer.id && (
                  <div className={`connection-status ${testResult.success ? 'success' : 'failed'}`}>
                    <span>{testResult.success ? '✓' : '✕'} {testResult.message}</span>
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="table-wrapper">
            <table className="servers-table">
              <thead>
                <tr>
                  <th className="col-expand"></th>
                  <th className="col-hostname">Hostname</th>
                  <th className="col-os">OS Type</th>
                  <th className="col-ip">IP Address</th>
                  <th className="col-health">Health</th>
                  <th className="col-patches">Patches</th>
                  <th className="col-compliance">Compliance</th>
                  <th className="col-actions">Actions</th>
                </tr>
              </thead>
              <tbody>
                {paginatedServers.map((server) => (
                  <React.Fragment key={server.id}>
                    <tr className={`server-row status-${server.connectivity_status}`}>
                      <td className="col-expand">
                        <button
                          className="expand-btn"
                          onClick={() => setExpandedServerId(expandedServerId === server.id ? null : server.id)}
                        >
                          {expandedServerId === server.id ? '▼' : '▶'}
                        </button>
                      </td>
                      <td className="col-hostname">
                        <div className="hostname-cell">
                          <span className="os-icon">{getOsIcon(server.os_type)}</span>
                          <button
                            className="hostname-text hostname-link"
                            onClick={() => onOpenServer?.(server.id)}
                            title="Open server details"
                          >{server.hostname}</button>
                        </div>
                      </td>
                      <td className="col-os">
                        <span className="os-badge">{server.os_type}</span>
                      </td>
                      <td className="col-ip">
                        <code>{server.ip_address || 'N/A'}</code>
                      </td>
                      <td className="col-health">
                        <div className="health-badge" style={{ borderColor: getHealthColor(server.connectivity_status) }}>
                          <span className="health-dot" style={{ backgroundColor: getHealthColor(server.connectivity_status) }} />
                          {getHealthLabel(server.connectivity_status)}
                        </div>
                      </td>
                      <td className="col-patches">
                        <span className="patch-badge">
                          {server.pending_scan_error ? <span style={{ color: '#c8514c' }} title={server.pending_scan_error}>scan failed</span> : server.pending_patch_count == null ? 'not scanned' : <span title={server.last_patch_scan_at ? `Scanned ${formatDateTime(server.last_patch_scan_at + 'Z')}` : ''}>{`${server.pending_patch_count} pending`}</span>}
                        </span>
                      </td>
                      <td className="col-compliance">
                        <div className="compliance-score" title="Compliance is not tracked yet">—</div>
                      </td>
                      <td className="col-actions">
                        <button
                          className="action-btn edit-btn"
                          title="Edit server"
                          onClick={() => handleEditServer(server)}
                        >✎</button>
                        <button
                          className="action-btn delete-btn"
                          title="Delete server"
                          onClick={() => handleDeleteServer(server.id)}
                          disabled={deletingServerId === server.id}
                        >{deletingServerId === server.id ? '⏳' : '✕'}</button>
                      </td>
                    </tr>

                    {/* Expandable Details Row */}
                    {expandedServerId === server.id && (
                      <tr className="details-row">
                        <td colSpan={8}>
                          <div className="server-details">
                            <div className="details-grid">
                              {/* System Information */}
                              <div className="detail-section">
                                <h4>System Information</h4>
                                <div className="detail-item">
                                  <span className="label">Created:</span>
                                  <span className="value">{formatDate(server.created_at)}</span>
                                </div>
                                <div className="detail-item">
                                  <span className="label">Auth Method:</span>
                                  <span className="value">{server.auth_method}</span>
                                </div>
                                <div className="detail-item">
                                  <span className="label">Port:</span>
                                  <span className="value">{server.port || '22'}</span>
                                </div>
                              </div>

                              {/* Health Metrics */}
                              <div className="detail-section">
                                <h4>Health Metrics</h4>
                                {server.health && !server.health.error ? (
                                  <>
                                    {[
                                      { label: 'CPU', value: server.health.cpu_pct },
                                      { label: 'Memory', value: server.health.memory_pct },
                                      { label: 'Disk (/)', value: server.health.disk_pct },
                                    ].map(m => (
                                      <div className="metric" key={m.label}>
                                        <div className="metric-label">{m.label}</div>
                                        <div className="metric-bar">
                                          <div className="metric-fill" style={{ width: `${Math.min(m.value ?? 0, 100)}%` }}>
                                            {m.value == null ? 'n/a' : `${m.value}%`}
                                          </div>
                                        </div>
                                      </div>
                                    ))}
                                    <div className="detail-item">
                                      <span className="label">Load (1m):</span>
                                      <span className="value">{server.health.load_1m} on {server.health.cpu_cores} core(s)</span>
                                    </div>
                                    <div className="detail-item">
                                      <span className="label">Uptime:</span>
                                      <span className="value">{Math.floor((server.health.uptime_seconds ?? 0) / 86400)} day(s)</span>
                                    </div>
                                    <div className="detail-item">
                                      <span className="label">Collected:</span>
                                      <span className="value">{formatDateTime(server.health.collected_at + 'Z')}</span>
                                    </div>
                                  </>
                                ) : (
                                  <div className="detail-item">
                                    <span className="label">Health:</span>
                                    <span className="value">{server.health?.error || 'not collected yet — runs with each scan'}</span>
                                  </div>
                                )}
                              </div>

                              {/* Patch Summary */}
                              <div className="detail-section">
                                <h4>Patch Summary</h4>
                                <div className="patch-summary">
                                  <div className="summary-item">
                                    <span className="label">Pending:</span>
                                    <span className="value">{server.pending_patch_count ?? 'not scanned'}</span>
                                  </div>
                                </div>
                              </div>

                              {/* Last Deployment */}
                              <div className="detail-section">
                                <h4>Recent Activity</h4>
                                <div className="detail-item">
                                  <span className="label">Last Scan:</span>
                                  <span className="value">{server.last_patch_scan_at ? formatDateTime(server.last_patch_scan_at + 'Z') : 'never'}</span>
                                </div>
                              </div>
                            </div>

                            <ServerBackups serverId={server.id} />

                            {/* Server Actions */}
                            <div className="server-actions">
                              <button
                                className="action-btn-lg test-btn"
                                onClick={() => handleTestExistingConnection(server.id)}
                                disabled={testingConnectionId === server.id}
                              >
                                {testingConnectionId === server.id ? '🔌 Testing...' : '🔌 Test Connection'}
                              </button>
                              <button
                                className="action-btn-lg scan-btn"
                                onClick={() => handleScanPatches(server.id)}
                                disabled={scanningServerId === server.id}
                              >
                                {scanningServerId === server.id ? '🔍 Scanning...' : '🔍 Scan Patches'}
                              </button>
                              <button
                                className="action-btn-lg detect-btn"
                                onClick={() => handleDetectVersionExisting(server.id)}
                                title="Auto-detect and update OS version"
                              >
                                ⬆️ Detect Version
                              </button>
                            </div>

                            {testResult && testResult.serverId === server.id && (
                              <div className={`status-message ${testResult.success ? 'success' : 'failed'}`}>
                                <span>{testResult.success ? '✓' : '✕'} {testResult.message}</span>
                              </div>
                            )}

                            {scanResult && scanResult.serverId === server.id && (
                              <div className="status-message success">
                                <span>
                                  ✓ {scanResult.count} package(s) upgradable
                                  {scanResult.refreshed === false && ' — package index could not be refreshed, so this may be out of date'}
                                </span>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {filteredServers.length > 0 && (
            <Pagination
              currentPage={currentPage}
              totalItems={filteredServers.length}
              pageSize={pageSize}
              onPageChange={setCurrentPage}
              onPageSizeChange={(size) => {
                setPageSize(size)
                setCurrentPage(1)
              }}
            />
          )}

          {filteredServers.length === 0 && (
            <div className="empty-state">
              <div className="empty-icon">🔍</div>
              <div className="empty-title">No servers found</div>
              <div className="empty-desc">Try adjusting your filters or search query</div>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

// Add React import for Fragment
import React from 'react'
import { useDefaultPageSize } from '../lib/defaultPageSize'
import { formatDateTime, formatDate } from '../lib/displayTime'
