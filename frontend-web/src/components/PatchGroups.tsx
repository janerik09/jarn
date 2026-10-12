import { useEffect, useState } from 'react'
import './PatchGroups.css'

interface Server {
  id: string
  hostname: string
  os_type: string
  os_version?: string
}

interface PatchGroup {
  id: string
  name: string
  description?: string
  patch_ids: string[]
  server_ids: string[]
  change_id?: string
  change_management_system?: string
  approval_status: string
  business_justification?: string
  risk_level?: string
  maintenance_window?: string
  is_active: boolean
  created_at: string
  scheduled_date?: string
}

interface AvailablePatch {
  patch_name: string
  servers_needing_patch: string[]
  server_count: number
}

interface AvailablePatchesResponse {
  group_id: string
  group_name: string
  total_servers: number
  available_patches: AvailablePatch[]
}

export function PatchGroups() {
  const [groups, setGroups] = useState<PatchGroup[]>([])
  const [servers, setServers] = useState<Server[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  // Create group form
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [createFormData, setCreateFormData] = useState({
    name: '',
    description: '',
    server_ids: [] as string[],
    risk_level: 'medium',
    business_justification: '',
    maintenance_window: ''
  })

  // Edit group form
  const [showEditForm, setShowEditForm] = useState(false)
  const [editingGroup, setEditingGroup] = useState<PatchGroup | null>(null)
  const [editFormData, setEditFormData] = useState({
    name: '',
    description: '',
    server_ids: [] as string[],
    risk_level: 'medium',
    business_justification: '',
    maintenance_window: ''
  })
  const [editingGroupId, setEditingGroupId] = useState<string | null>(null)

  // Delete group
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [groupToDelete, setGroupToDelete] = useState<PatchGroup | null>(null)
  const [deletingGroup, setDeletingGroup] = useState(false)

  // Create job modal
  const [showJobModal, setShowJobModal] = useState(false)
  const [selectedGroup, setSelectedGroup] = useState<PatchGroup | null>(null)
  const [jobFormData, setJobFormData] = useState({
    change_id: '',
    job_name: '',
    patches: [] as string[],
    priority: 'medium',
    dry_run: false,
    auto_rollback: true
  })
  const [crStatus, setCRStatus] = useState<{ status: string; message: string } | null>(null)
  const [crVerifying, setCRVerifying] = useState(false)
  const [creatingJob, setCreatingJob] = useState(false)
  const [availablePatches, setAvailablePatches] = useState<AvailablePatch[]>([])
  const [loadingPatches, setLoadingPatches] = useState(false)

  useEffect(() => {
    fetchData()
  }, [])

  const fetchData = async () => {
    try {
      setLoading(true)
      const [groupsRes, serversRes] = await Promise.all([
        fetch(`/api/v1/patch-groups`),
        fetch(`/api/v1/servers`)
      ])
      const groupsData = await groupsRes.json()
      const serversData = await serversRes.json()
      setGroups(groupsData)
      setServers(serversData)
      setError(null)
    } catch (err) {
      setError(`Failed to load data: ${err}`)
    } finally {
      setLoading(false)
    }
  }

  const handleCreateGroup = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      if (!createFormData.name.trim()) {
        setError('Group name is required')
        return
      }
      if (createFormData.server_ids.length === 0) {
        setError('Select at least one server')
        return
      }

      const response = await fetch(`/api/v1/patch-groups`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...createFormData,
          patch_ids: [] // Patches selected during job creation
        })
      })

      if (response.ok) {
        setSuccess(`✓ Server group "${createFormData.name}" created`)
        setShowCreateForm(false)
        setCreateFormData({
          name: '',
          description: '',
          server_ids: [],
          risk_level: 'medium',
          business_justification: '',
          maintenance_window: ''
        })
        fetchData()
      } else {
        const data = await response.json()
        setError(data.detail || 'Failed to create server group')
      }
    } catch (err) {
      setError(`Error creating group: ${err}`)
    }
  }

  const handleOpenJobModal = async (group: PatchGroup) => {
    setSelectedGroup(group)
    setJobFormData({
      change_id: '',
      job_name: '',
      patches: [],
      priority: 'medium',
      dry_run: false,
      auto_rollback: true
    })
    setCRStatus(null)
    setShowJobModal(true)

    // Fetch available patches
    await fetchAvailablePatches(group.id)
  }

  const fetchAvailablePatches = async (groupId: string) => {
    try {
      setLoadingPatches(true)
      const response = await fetch(`/api/v1/patch-groups/${groupId}/available-patches`)
      if (response.ok) {
        const data: AvailablePatchesResponse = await response.json()
        setAvailablePatches(data.available_patches || [])
      } else {
        setAvailablePatches([])
      }
    } catch (err) {
      console.error('Failed to fetch available patches:', err)
      setAvailablePatches([])
    } finally {
      setLoadingPatches(false)
    }
  }

  const handleVerifyChangeRequest = async () => {
    if (!jobFormData.change_id.trim()) {
      setError('Enter a change request number')
      return
    }

    setCRVerifying(true)
    try {
      const response = await fetch(`/api/v1/patch-groups/verify-change-request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ change_id: jobFormData.change_id })
      })

      const data = await response.json()
      setCRStatus({
        status: data.status,
        message: data.message
      })
    } catch (err) {
      setCRStatus({
        status: 'error',
        message: `Failed to verify: ${err}`
      })
    } finally {
      setCRVerifying(false)
    }
  }

  const handleCreateJob = async () => {
    if (!selectedGroup) return
    if (!jobFormData.change_id.trim()) {
      setError('Change request ID required')
      return
    }
    if (!jobFormData.job_name.trim()) {
      setError('Job name required')
      return
    }
    if (jobFormData.patches.length === 0) {
      setError('Select at least one patch')
      return
    }
    if (crStatus?.status !== 'approved') {
      setError('Change request must be approved')
      return
    }

    setCreatingJob(true)
    try {
      const response = await fetch(
        `/api/v1/patch-groups/${selectedGroup.id}/create-job`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            patch_group_id: selectedGroup.id,
            patch_ids: jobFormData.patches,
            change_id: jobFormData.change_id,
            job_name: jobFormData.job_name,
            priority: jobFormData.priority,
            dry_run: jobFormData.dry_run,
            auto_rollback: jobFormData.auto_rollback
          })
        }
      )

      if (response.ok) {
        setSuccess(`✓ Patch job "${jobFormData.job_name}" created with ${selectedGroup.server_ids.length} servers`)
        setShowJobModal(false)
        fetchData()
      } else {
        const data = await response.json()
        setError(data.detail || 'Failed to create job')
      }
    } catch (err) {
      setError(`Error creating job: ${err}`)
    } finally {
      setCreatingJob(false)
    }
  }

  const getServerNames = (serverIds: string[]) => {
    return serverIds
      .map(id => servers.find(s => s.id === id)?.hostname)
      .filter(Boolean)
      .join(', ')
  }

  const toggleServerSelection = (serverId: string) => {
    setCreateFormData(prev => ({
      ...prev,
      server_ids: prev.server_ids.includes(serverId)
        ? prev.server_ids.filter(id => id !== serverId)
        : [...prev.server_ids, serverId]
    }))
  }

  const togglePatchSelection = (patchName: string) => {
    setJobFormData(prev => ({
      ...prev,
      patches: prev.patches.includes(patchName)
        ? prev.patches.filter(p => p !== patchName)
        : [...prev.patches, patchName]
    }))
  }

  const handleOpenEditForm = (group: PatchGroup) => {
    setEditingGroup(group)
    setEditingGroupId(group.id)
    setEditFormData({
      name: group.name,
      description: group.description || '',
      server_ids: [...group.server_ids],
      risk_level: group.risk_level || 'medium',
      business_justification: group.business_justification || '',
      maintenance_window: group.maintenance_window || ''
    })
    setShowEditForm(true)
  }

  const handleEditGroup = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!editingGroupId) return

    try {
      if (!editFormData.name.trim()) {
        setError('Group name is required')
        return
      }
      if (editFormData.server_ids.length === 0) {
        setError('Select at least one server')
        return
      }

      const response = await fetch(`/api/v1/patch-groups/${editingGroupId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(editFormData)
      })

      if (response.ok) {
        setSuccess(`✓ Server group "${editFormData.name}" updated`)
        setShowEditForm(false)
        setEditingGroupId(null)
        setEditingGroup(null)
        fetchData()
      } else {
        const data = await response.json()
        setError(data.detail || 'Failed to update server group')
      }
    } catch (err) {
      setError(`Error updating group: ${err}`)
    }
  }

  const toggleEditServerSelection = (serverId: string) => {
    setEditFormData(prev => ({
      ...prev,
      server_ids: prev.server_ids.includes(serverId)
        ? prev.server_ids.filter(id => id !== serverId)
        : [...prev.server_ids, serverId]
    }))
  }

  const handleOpenDeleteConfirm = (group: PatchGroup) => {
    setGroupToDelete(group)
    setShowDeleteConfirm(true)
  }

  const handleDeleteGroup = async () => {
    if (!groupToDelete) return

    setDeletingGroup(true)
    try {
      const response = await fetch(`/api/v1/patch-groups/${groupToDelete.id}`, {
        method: 'DELETE'
      })

      if (response.ok) {
        setSuccess(`✓ Server group "${groupToDelete.name}" deleted`)
        setShowDeleteConfirm(false)
        setGroupToDelete(null)
        fetchData()
      } else {
        const data = await response.json()
        setError(data.detail || 'Failed to delete server group')
      }
    } catch (err) {
      setError(`Error deleting group: ${err}`)
    } finally {
      setDeletingGroup(false)
    }
  }

  if (loading) {
    return <div className="patch-groups-loading">Loading server groups...</div>
  }

  return (
    <div className="patch-groups">
      <div className="patch-groups-header">
        <h2>Server Groups for Patching</h2>
        <button
          className="btn-create-group"
          onClick={() => setShowCreateForm(!showCreateForm)}
        >
          {showCreateForm ? '✕ Cancel' : '+ Create Server Group'}
        </button>
      </div>

      {error && (
        <div style={{
          background: 'rgba(200, 81, 76, 0.2)',
          color: '#c8514c',
          padding: '12px',
          borderRadius: '2px',
          marginBottom: '15px',
          border: '1px solid #c8514c'
        }}>
          {error}
        </div>
      )}

      {success && (
        <div style={{
          background: 'rgba(58, 157, 110, 0.2)',
          color: '#3a9d6e',
          padding: '12px',
          borderRadius: '2px',
          marginBottom: '15px',
          border: '1px solid #3a9d6e'
        }}>
          {success}
        </div>
      )}

      {showCreateForm && (
        <div style={{
          background: 'rgba(15, 19, 25, 0.3)',
          border: '2px solid rgba(74, 143, 212, 0.5)',
          borderRadius: '4px',
          padding: '20px',
          marginBottom: '20px'
        }}>
          <form onSubmit={handleCreateGroup}>
            <div style={{ marginBottom: '15px' }}>
              <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                Group Name *
              </label>
              <input
                type="text"
                value={createFormData.name}
                onChange={(e) => setCreateFormData({ ...createFormData, name: e.target.value })}
                placeholder="e.g., Q4 Security Updates - Web Servers"
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  background: 'rgba(74, 143, 212, 0.06)',
                  border: '2px solid rgba(74, 143, 212, 0.25)',
                  color: '#8fbce6',
                  fontFamily: "'Courier New', monospace",
                  boxSizing: 'border-box',
                  borderRadius: '2px'
                }}
              />
            </div>

            <div style={{ marginBottom: '15px' }}>
              <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                Description
              </label>
              <textarea
                value={createFormData.description}
                onChange={(e) => setCreateFormData({ ...createFormData, description: e.target.value })}
                placeholder="Describe this server group"
                rows={3}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  background: 'rgba(74, 143, 212, 0.06)',
                  border: '2px solid rgba(74, 143, 212, 0.25)',
                  color: '#8fbce6',
                  fontFamily: "'Courier New', monospace",
                  boxSizing: 'border-box',
                  borderRadius: '2px'
                }}
              />
            </div>

            <div style={{ marginBottom: '15px' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontWeight: 'bold' }}>
                Select Servers * ({createFormData.server_ids.length} selected)
              </label>
              <div style={{
                background: 'rgba(0, 0, 0, 0.2)',
                border: '2px solid rgba(58, 157, 110, 0.3)',
                borderRadius: '2px',
                padding: '10px',
                maxHeight: '200px',
                overflowY: 'auto'
              }}>
                {servers.length === 0 ? (
                  <p style={{ color: '#8b95a5', margin: '10px 0' }}>No servers available</p>
                ) : (
                  servers.map(server => (
                    <label
                      key={server.id}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        padding: '8px',
                        cursor: 'pointer',
                        color: '#4a8fd4'
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={createFormData.server_ids.includes(server.id)}
                        onChange={() => toggleServerSelection(server.id)}
                        style={{ marginRight: '8px', cursor: 'pointer' }}
                      />
                      <span>{server.hostname} ({server.os_type})</span>
                    </label>
                  ))
                )}
              </div>
            </div>

            <div style={{ marginBottom: '15px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px' }}>
              <div>
                <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                  Risk Level
                </label>
                <select
                  value={createFormData.risk_level}
                  onChange={(e) => setCreateFormData({ ...createFormData, risk_level: e.target.value })}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    background: 'rgba(15, 19, 25, 0.8)',
                    border: '1px solid rgba(212, 161, 50, 0.2)',
                    color: '#e6ebf1',
                    fontFamily: 'inherit',
                    borderRadius: '4px',
                    cursor: 'pointer'
                  }}
                >
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                  <option value="critical">Critical</option>
                </select>
              </div>
              <div>
                <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                  Maintenance Window
                </label>
                <input
                  type="text"
                  value={createFormData.maintenance_window}
                  onChange={(e) => setCreateFormData({ ...createFormData, maintenance_window: e.target.value })}
                  placeholder="e.g., Saturday 2AM-4AM UTC"
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    background: 'rgba(74, 143, 212, 0.06)',
                    border: '2px solid rgba(74, 143, 212, 0.25)',
                    color: '#8fbce6',
                    fontFamily: "'Courier New', monospace",
                    borderRadius: '2px'
                  }}
                />
              </div>
            </div>

            <div style={{ marginBottom: '15px' }}>
              <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                Business Justification
              </label>
              <textarea
                value={createFormData.business_justification}
                onChange={(e) => setCreateFormData({ ...createFormData, business_justification: e.target.value })}
                placeholder="Explain the business need for patching this group"
                rows={3}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  background: 'rgba(74, 143, 212, 0.06)',
                  border: '2px solid rgba(74, 143, 212, 0.25)',
                  color: '#8fbce6',
                  fontFamily: "'Courier New', monospace",
                  boxSizing: 'border-box',
                  borderRadius: '2px'
                }}
              />
            </div>

            <button
              type="submit"
              style={{
                padding: '10px 20px',
                background: 'rgba(58, 157, 110, 0.2)',
                border: '2px solid #3a9d6e',
                color: '#3a9d6e',
                cursor: 'pointer',
                borderRadius: '2px',
                fontWeight: 'bold',
                textTransform: 'uppercase',
                fontSize: '0.85em'
              }}
            >
              Create Server Group
            </button>
          </form>
        </div>
      )}

      {groups.length === 0 ? (
        <div style={{
          textAlign: 'center',
          padding: '40px 20px',
          color: '#8b95a5'
        }}>
          <div style={{ fontSize: '3em', marginBottom: '10px' }}>👥</div>
          <h3>No server groups yet</h3>
          <p>Create a server group to organize servers for patching</p>
        </div>
      ) : (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(400px, 1fr))',
          gap: '15px'
        }}>
          {groups.map(group => (
            <div
              key={group.id}
              style={{
                background: 'rgba(15, 19, 25, 0.3)',
                border: '2px solid rgba(74, 143, 212, 0.5)',
                borderRadius: '4px',
                padding: '15px'
              }}
            >
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'start',
                marginBottom: '10px'
              }}>
                <h3 style={{ margin: 0, color: '#3a9d6e' }}>{group.name}</h3>
                <span style={{
                  background: group.approval_status === 'approved' ? 'rgba(58, 157, 110, 0.3)' : 'rgba(212, 161, 50, 0.3)',
                  color: group.approval_status === 'approved' ? '#3a9d6e' : '#d4a132',
                  padding: '4px 8px',
                  borderRadius: '2px',
                  fontSize: '0.8em',
                  fontWeight: 'bold'
                }}>
                  {group.approval_status.toUpperCase()}
                </span>
              </div>

              {group.description && (
                <p style={{ color: '#8b95a5', fontSize: '0.9em', marginBottom: '10px' }}>
                  {group.description}
                </p>
              )}

              <div style={{ color: '#3a9d6e', fontSize: '0.9em', marginBottom: '10px' }}>
                <div>📊 {group.server_ids.length} servers</div>
                {group.risk_level && <div>⚠️ Risk: {group.risk_level}</div>}
                {group.maintenance_window && <div>🕐 {group.maintenance_window}</div>}
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px', marginBottom: '10px' }}>
                <button
                  onClick={() => handleOpenJobModal(group)}
                  style={{
                    padding: '8px',
                    background: 'rgba(58, 157, 110, 0.2)',
                    border: '2px solid #3a9d6e',
                    color: '#3a9d6e',
                    cursor: 'pointer',
                    borderRadius: '2px',
                    fontWeight: 'bold',
                    textTransform: 'uppercase',
                    fontSize: '0.75em'
                  }}
                >
                  Create Job
                </button>
                <button
                  onClick={() => handleOpenEditForm(group)}
                  style={{
                    padding: '8px',
                    background: 'rgba(74, 143, 212, 0.2)',
                    border: '2px solid #4a8fd4',
                    color: '#4a8fd4',
                    cursor: 'pointer',
                    borderRadius: '2px',
                    fontWeight: 'bold',
                    textTransform: 'uppercase',
                    fontSize: '0.75em'
                  }}
                >
                  Edit
                </button>
                <button
                  onClick={() => handleOpenDeleteConfirm(group)}
                  style={{
                    padding: '8px',
                    background: 'rgba(200, 81, 76, 0.2)',
                    border: '2px solid #c8514c',
                    color: '#c8514c',
                    cursor: 'pointer',
                    borderRadius: '2px',
                    fontWeight: 'bold',
                    textTransform: 'uppercase',
                    fontSize: '0.75em'
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showEditForm && editingGroup && (
        <div style={{
          background: 'rgba(15, 19, 25, 0.3)',
          border: '2px solid rgba(74, 143, 212, 0.5)',
          borderRadius: '4px',
          padding: '20px',
          marginBottom: '20px'
        }}>
          <form onSubmit={handleEditGroup}>
            <h3 style={{ marginTop: 0, color: '#3a9d6e' }}>Edit Server Group</h3>

            <div style={{ marginBottom: '15px' }}>
              <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                Group Name *
              </label>
              <input
                type="text"
                value={editFormData.name}
                onChange={(e) => setEditFormData({ ...editFormData, name: e.target.value })}
                placeholder="e.g., Q4 Security Updates - Web Servers"
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  background: 'rgba(74, 143, 212, 0.06)',
                  border: '2px solid rgba(74, 143, 212, 0.25)',
                  color: '#8fbce6',
                  fontFamily: "'Courier New', monospace",
                  boxSizing: 'border-box',
                  borderRadius: '2px'
                }}
              />
            </div>

            <div style={{ marginBottom: '15px' }}>
              <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                Description
              </label>
              <textarea
                value={editFormData.description}
                onChange={(e) => setEditFormData({ ...editFormData, description: e.target.value })}
                placeholder="Describe this server group"
                rows={3}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  background: 'rgba(74, 143, 212, 0.06)',
                  border: '2px solid rgba(74, 143, 212, 0.25)',
                  color: '#8fbce6',
                  fontFamily: "'Courier New', monospace",
                  boxSizing: 'border-box',
                  borderRadius: '2px'
                }}
              />
            </div>

            <div style={{ marginBottom: '15px' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontWeight: 'bold' }}>
                Select Servers * ({editFormData.server_ids.length} selected)
              </label>
              <div style={{
                background: 'rgba(0, 0, 0, 0.2)',
                border: '2px solid rgba(58, 157, 110, 0.3)',
                borderRadius: '2px',
                padding: '10px',
                maxHeight: '200px',
                overflowY: 'auto'
              }}>
                {servers.length === 0 ? (
                  <p style={{ color: '#8b95a5', margin: '10px 0' }}>No servers available</p>
                ) : (
                  servers.map(server => (
                    <label
                      key={server.id}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        padding: '8px',
                        cursor: 'pointer',
                        color: '#4a8fd4'
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={editFormData.server_ids.includes(server.id)}
                        onChange={() => toggleEditServerSelection(server.id)}
                        style={{ marginRight: '8px', cursor: 'pointer' }}
                      />
                      <span>{server.hostname} ({server.os_type})</span>
                    </label>
                  ))
                )}
              </div>
            </div>

            <div style={{ marginBottom: '15px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px' }}>
              <div>
                <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                  Risk Level
                </label>
                <select
                  value={editFormData.risk_level}
                  onChange={(e) => setEditFormData({ ...editFormData, risk_level: e.target.value })}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    background: 'rgba(15, 19, 25, 0.8)',
                    border: '1px solid rgba(212, 161, 50, 0.2)',
                    color: '#e6ebf1',
                    fontFamily: 'inherit',
                    borderRadius: '4px',
                    cursor: 'pointer'
                  }}
                >
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                  <option value="critical">Critical</option>
                </select>
              </div>
              <div>
                <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                  Maintenance Window
                </label>
                <input
                  type="text"
                  value={editFormData.maintenance_window}
                  onChange={(e) => setEditFormData({ ...editFormData, maintenance_window: e.target.value })}
                  placeholder="e.g., Saturday 2AM-4AM UTC"
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    background: 'rgba(74, 143, 212, 0.06)',
                    border: '2px solid rgba(74, 143, 212, 0.25)',
                    color: '#8fbce6',
                    fontFamily: "'Courier New', monospace",
                    borderRadius: '2px'
                  }}
                />
              </div>
            </div>

            <div style={{ marginBottom: '15px' }}>
              <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                Business Justification
              </label>
              <textarea
                value={editFormData.business_justification}
                onChange={(e) => setEditFormData({ ...editFormData, business_justification: e.target.value })}
                placeholder="Explain the business need for patching this group"
                rows={3}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  background: 'rgba(74, 143, 212, 0.06)',
                  border: '2px solid rgba(74, 143, 212, 0.25)',
                  color: '#8fbce6',
                  fontFamily: "'Courier New', monospace",
                  boxSizing: 'border-box',
                  borderRadius: '2px'
                }}
              />
            </div>

            <div style={{ display: 'flex', gap: '10px' }}>
              <button
                type="submit"
                style={{
                  flex: 1,
                  padding: '10px 20px',
                  background: 'rgba(58, 157, 110, 0.2)',
                  border: '2px solid #3a9d6e',
                  color: '#3a9d6e',
                  cursor: 'pointer',
                  borderRadius: '2px',
                  fontWeight: 'bold',
                  textTransform: 'uppercase',
                  fontSize: '0.85em'
                }}
              >
                Save Changes
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowEditForm(false)
                  setEditingGroupId(null)
                  setEditingGroup(null)
                }}
                style={{
                  flex: 1,
                  padding: '10px 20px',
                  background: 'rgba(200, 81, 76, 0.2)',
                  border: '2px solid #c8514c',
                  color: '#c8514c',
                  cursor: 'pointer',
                  borderRadius: '2px',
                  fontWeight: 'bold',
                  textTransform: 'uppercase',
                  fontSize: '0.85em'
                }}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {showDeleteConfirm && groupToDelete && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(0, 0, 0, 0.7)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1001
        }}>
          <div style={{
            background: 'rgba(15, 19, 25, 0.95)',
            border: '2px solid rgba(200, 81, 76, 0.5)',
            borderRadius: '4px',
            padding: '25px',
            maxWidth: '400px',
            color: '#c8514c'
          }}>
            <h2 style={{ marginTop: 0, marginBottom: '20px' }}>Confirm Delete</h2>
            <p style={{ marginBottom: '20px', color: '#8b95a5' }}>
              Are you sure you want to delete the server group <strong>"{groupToDelete.name}"</strong>? This action cannot be undone.
            </p>
            <div style={{ display: 'flex', gap: '10px' }}>
              <button
                onClick={handleDeleteGroup}
                disabled={deletingGroup}
                style={{
                  flex: 1,
                  padding: '10px',
                  background: 'rgba(200, 81, 76, 0.2)',
                  border: '2px solid #c8514c',
                  color: '#c8514c',
                  cursor: deletingGroup ? 'wait' : 'pointer',
                  borderRadius: '2px',
                  fontWeight: 'bold',
                  textTransform: 'uppercase',
                  opacity: deletingGroup ? 0.5 : 1
                }}
              >
                {deletingGroup ? 'Deleting...' : 'Delete'}
              </button>
              <button
                onClick={() => {
                  setShowDeleteConfirm(false)
                  setGroupToDelete(null)
                }}
                disabled={deletingGroup}
                style={{
                  flex: 1,
                  padding: '10px',
                  background: 'rgba(74, 143, 212, 0.2)',
                  border: '2px solid rgba(74, 143, 212, 0.5)',
                  color: '#4a8fd4',
                  cursor: deletingGroup ? 'wait' : 'pointer',
                  borderRadius: '2px',
                  fontWeight: 'bold',
                  textTransform: 'uppercase',
                  opacity: deletingGroup ? 0.5 : 1
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {showJobModal && selectedGroup && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(0, 0, 0, 0.7)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1000
        }}>
          <div style={{
            background: 'rgba(15, 19, 25, 0.95)',
            border: '2px solid rgba(74, 143, 212, 0.5)',
            borderRadius: '4px',
            padding: '25px',
            maxWidth: '600px',
            maxHeight: '90vh',
            overflowY: 'auto',
            color: '#e6eaf0'
          }}>
            <h2 style={{ marginTop: 0, marginBottom: '20px' }}>Create Patch Job</h2>

            <div style={{ marginBottom: '15px', padding: '10px', background: 'rgba(74, 143, 212, 0.2)', borderRadius: '2px' }}>
              <strong>Group:</strong> {selectedGroup.name}<br />
              <strong>Servers:</strong> {selectedGroup.server_ids.length}<br />
              <small style={{ color: '#8b95a5' }}>{getServerNames(selectedGroup.server_ids)}</small>
            </div>

            <div style={{ marginBottom: '15px' }}>
              <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                Change Request # *
              </label>
              <input
                type="text"
                value={jobFormData.change_id}
                onChange={(e) => setJobFormData({ ...jobFormData, change_id: e.target.value })}
                placeholder="e.g., CHG-2026-001"
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  background: 'rgba(74, 143, 212, 0.06)',
                  border: '2px solid rgba(74, 143, 212, 0.25)',
                  color: '#8fbce6',
                  fontFamily: "'Courier New', monospace",
                  boxSizing: 'border-box',
                  borderRadius: '2px'
                }}
              />
              <button
                onClick={handleVerifyChangeRequest}
                disabled={crVerifying || !jobFormData.change_id.trim()}
                style={{
                  marginTop: '8px',
                  padding: '6px 12px',
                  background: crVerifying ? 'rgba(15, 19, 25, 0.3)' : 'rgba(74, 143, 212, 0.3)',
                  border: '2px solid rgba(74, 143, 212, 0.5)',
                  color: '#4a8fd4',
                  cursor: crVerifying ? 'wait' : 'pointer',
                  borderRadius: '2px',
                  fontSize: '0.85em',
                  opacity: crVerifying || !jobFormData.change_id.trim() ? 0.5 : 1
                }}
              >
                {crVerifying ? 'Verifying...' : 'Verify CR'}
              </button>
              {crStatus && (
                <div style={{
                  marginTop: '8px',
                  padding: '8px',
                  background: crStatus.status === 'approved' ? 'rgba(58, 157, 110, 0.2)' : 'rgba(200, 81, 76, 0.2)',
                  border: `2px solid ${crStatus.status === 'approved' ? 'rgba(58, 157, 110, 0.5)' : 'rgba(200, 81, 76, 0.5)'}`,
                  borderRadius: '2px',
                  color: crStatus.status === 'approved' ? '#3a9d6e' : '#c8514c'
                }}>
                  {crStatus.message}
                </div>
              )}
            </div>

            <div style={{ marginBottom: '15px' }}>
              <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                Job Name *
              </label>
              <input
                type="text"
                value={jobFormData.job_name}
                onChange={(e) => setJobFormData({ ...jobFormData, job_name: e.target.value })}
                placeholder="e.g., Q4 Security Updates - Batch 1"
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  background: 'rgba(74, 143, 212, 0.06)',
                  border: '2px solid rgba(74, 143, 212, 0.25)',
                  color: '#8fbce6',
                  fontFamily: "'Courier New', monospace",
                  boxSizing: 'border-box',
                  borderRadius: '2px'
                }}
              />
            </div>

            <div style={{ marginBottom: '15px' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontWeight: 'bold' }}>
                Select Patches * ({jobFormData.patches.length} selected)
              </label>
              <div style={{
                background: 'rgba(0, 0, 0, 0.2)',
                border: '2px solid rgba(58, 157, 110, 0.3)',
                borderRadius: '2px',
                padding: '10px',
                maxHeight: '200px',
                overflowY: 'auto'
              }}>
                {loadingPatches ? (
                  <div style={{ color: '#8b95a5', textAlign: 'center', padding: '15px' }}>
                    Loading available patches...
                  </div>
                ) : availablePatches.length === 0 ? (
                  <div style={{ color: '#8b95a5', padding: '10px 0' }}>
                    <p style={{ marginTop: 0 }}>No patches available for this server group</p>
                  </div>
                ) : (
                  availablePatches.map(patch => (
                    <label
                      key={patch.patch_name}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        padding: '8px',
                        cursor: 'pointer',
                        color: '#3a9d6e',
                        marginBottom: '4px'
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={jobFormData.patches.includes(patch.patch_name)}
                        onChange={() => togglePatchSelection(patch.patch_name)}
                        style={{ marginRight: '8px', cursor: 'pointer' }}
                      />
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 'bold' }}>{patch.patch_name}</div>
                        <div style={{ fontSize: '0.8em', color: '#8b95a5' }}>
                          {patch.server_count}/{selectedGroup?.server_ids.length || 0} servers need this patch
                          {patch.servers_needing_patch && patch.servers_needing_patch.length > 0 && (
                            <div style={{ marginTop: '2px', color: '#8b95a5', fontSize: '0.75em' }}>
                              ({patch.servers_needing_patch.join(', ')})
                            </div>
                          )}
                        </div>
                      </div>
                    </label>
                  ))
                )}
              </div>
            </div>

            <div style={{ marginBottom: '15px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px' }}>
              <div>
                <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                  Priority
                </label>
                <select
                  value={jobFormData.priority}
                  onChange={(e) => setJobFormData({ ...jobFormData, priority: e.target.value })}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    background: 'rgba(74, 143, 212, 0.06)',
                    border: '2px solid rgba(74, 143, 212, 0.25)',
                    color: '#8fbce6',
                    fontFamily: "'Courier New', monospace",
                    borderRadius: '2px',
                    cursor: 'pointer'
                  }}
                >
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </div>
              <div>
                <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 'bold', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={jobFormData.auto_rollback}
                    onChange={(e) => setJobFormData({ ...jobFormData, auto_rollback: e.target.checked })}
                    style={{ cursor: 'pointer' }}
                  />
                  Auto Rollback
                </label>
              </div>
            </div>

            <div style={{ marginBottom: '20px' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 'bold', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={jobFormData.dry_run}
                  onChange={(e) => setJobFormData({ ...jobFormData, dry_run: e.target.checked })}
                  style={{ cursor: 'pointer' }}
                />
                Dry Run (test without applying)
              </label>
            </div>

            <div style={{ display: 'flex', gap: '10px' }}>
              <button
                onClick={handleCreateJob}
                disabled={creatingJob || crStatus?.status !== 'approved' || !jobFormData.job_name.trim() || jobFormData.patches.length === 0}
                style={{
                  flex: 1,
                  padding: '10px',
                  background: creatingJob || crStatus?.status !== 'approved' ? 'rgba(15, 19, 25, 0.3)' : 'rgba(58, 157, 110, 0.2)',
                  border: '2px solid #3a9d6e',
                  color: '#3a9d6e',
                  cursor: creatingJob || crStatus?.status !== 'approved' ? 'wait' : 'pointer',
                  borderRadius: '2px',
                  fontWeight: 'bold',
                  textTransform: 'uppercase',
                  opacity: creatingJob || crStatus?.status !== 'approved' ? 0.5 : 1
                }}
              >
                {creatingJob ? 'Creating...' : 'Create Job'}
              </button>
              <button
                onClick={() => setShowJobModal(false)}
                style={{
                  flex: 1,
                  padding: '10px',
                  background: 'rgba(200, 81, 76, 0.2)',
                  border: '2px solid #c8514c',
                  color: '#c8514c',
                  cursor: 'pointer',
                  borderRadius: '2px',
                  fontWeight: 'bold',
                  textTransform: 'uppercase'
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
