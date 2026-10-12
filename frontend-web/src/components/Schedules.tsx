import { useState, useEffect } from 'react'
import './Schedules.css'
import { listTimezones, useDisplayTimezone } from '../lib/displayTime'

interface Schedule {
  id: string
  name: string
  description?: string
  cron_expression: string
  timezone: string
  duration_minutes: string
  server_ids: string[]
  patch_filters?: Record<string, any>
  is_active: boolean
  action?: string
  last_run?: { at: string; result: 'started' | 'skipped' | 'failed'; detail: string; job_id: string | null } | null
  created_at: string
  updated_at: string
}

interface Server {
  id: string
  hostname: string
}

export function Schedules() {
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [servers, setServers] = useState<Server[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const displayZone = useDisplayTimezone()
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    cron_expression: '0 2 * * 0',
    timezone: displayZone,
    duration_minutes: '120',
    server_ids: [] as string[],
  })

  useEffect(() => {
    fetchSchedules()
    fetchServers()
  }, [])

  const fetchSchedules = async () => {
    try {
      const response = await fetch(`/api/v1/schedules`)
      const data = await response.json()
      setSchedules(data.schedules || [])
    } catch (err) {
      setError('Failed to load schedules')
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const fetchServers = async () => {
    try {
      const response = await fetch(`/api/v1/servers`)
      const data = await response.json()
      setServers(data)
    } catch (err) {
      console.error('Failed to load servers:', err)
    }
  }

  const handleSave = async () => {
    if (!formData.name.trim()) {
      setError('Schedule name is required')
      return
    }

    try {
      const method = editingId ? 'PUT' : 'POST'
      const url = editingId
        ? `/api/v1/schedules/${editingId}`
        : `/api/v1/schedules`

      const response = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...formData,
          server_ids: formData.server_ids,
        }),
      })

      if (!response.ok) {
        throw new Error('Failed to save schedule')
      }

      setShowForm(false)
      setEditingId(null)
      setFormData({
        name: '',
        description: '',
        cron_expression: '0 2 * * 0',
        timezone: displayZone,
        duration_minutes: '120',
        server_ids: [],
      })
      fetchSchedules()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save schedule')
    }
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm('Are you sure you want to delete this schedule?')) return

    try {
      const response = await fetch(`/api/v1/schedules/${id}`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        throw new Error('Failed to delete schedule')
      }

      fetchSchedules()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete schedule')
    }
  }

  const handleToggleActive = async (schedule: Schedule) => {
    try {
      const response = await fetch(`/api/v1/schedules/${schedule.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...schedule,
          is_active: !schedule.is_active,
        }),
      })

      if (!response.ok) {
        throw new Error('Failed to update schedule')
      }

      fetchSchedules()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update schedule')
    }
  }

  const handleEdit = (schedule: Schedule) => {
    setFormData({
      name: schedule.name,
      description: schedule.description || '',
      cron_expression: schedule.cron_expression,
      timezone: schedule.timezone,
      duration_minutes: schedule.duration_minutes,
      server_ids: schedule.server_ids,
    })
    setEditingId(schedule.id)
    setShowForm(true)
  }

  const getCronDescription = (cron: string) => {
    const parts = cron.split(' ')
    if (parts.length === 5) {
      const [minute, hour, , , dayOfWeek] = parts
      const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
      const dayName = dayOfWeek !== '*' ? dayNames[parseInt(dayOfWeek) || 0] : 'any day'
      return `${hour}:${minute.padStart(2, '0')} on ${dayName}`
    }
    return cron
  }

  if (loading) {
    return <div className="schedules-loading">Loading schedules...</div>
  }

  return (
    <div className="schedules-container">
      <div className="schedules-header">
        <h1>Maintenance Schedules</h1>
        <button className="btn-add" onClick={() => { setShowForm(true); setEditingId(null) }}>
          + Add Schedule
        </button>
      </div>

      {error && (
        <div className="error-message">
          <span>{error}</span>
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {showForm && (
        <div className="schedule-form-container">
          <div className="schedule-form">
            <h2>{editingId ? 'Edit Schedule' : 'Create Schedule'}</h2>

            <div className="form-group">
              <label>Schedule Name *</label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="e.g., Weekly Security Updates"
                className="form-input"
              />
            </div>

            <div className="form-group">
              <label>Description</label>
              <textarea
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="Optional description"
                className="form-input"
                rows={3}
              />
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>Cron Expression *</label>
                <input
                  type="text"
                  value={formData.cron_expression}
                  onChange={(e) => setFormData({ ...formData, cron_expression: e.target.value })}
                  placeholder="0 2 * * 0"
                  className="form-input"
                  title="Format: minute hour day month dayOfWeek (0=Sunday)"
                />
                <small>{getCronDescription(formData.cron_expression)}</small>
              </div>

              <div className="form-group">
                <label>Timezone</label>
                <select
                  value={formData.timezone}
                  onChange={(e) => setFormData({ ...formData, timezone: e.target.value })}
                  className="form-input"
                >
                  {listTimezones().map(zone => (
                    <option key={zone} value={zone}>{zone}</option>
                  ))}
                </select>
                <small>The cron times are read in this timezone.</small>
              </div>
            </div>

            <p className="form-note">
              Each run installs all pending updates on the selected servers. It uses the same approval checks as a
              manual job, so a run that needs approval is skipped and reported to you instead of running.
            </p>

            <div className="form-group">
              <label>Target Servers</label>
              <div className="server-list">
                {servers.map(server => (
                  <label key={server.id} className="server-checkbox">
                    <input
                      type="checkbox"
                      checked={formData.server_ids.includes(server.id)}
                      onChange={(e) => {
                        const updated = e.target.checked
                          ? [...formData.server_ids, server.id]
                          : formData.server_ids.filter(id => id !== server.id)
                        setFormData({ ...formData, server_ids: updated })
                      }}
                    />
                    <span>{server.hostname}</span>
                  </label>
                ))}
              </div>
            </div>

            <div className="form-actions">
              <button className="btn-cancel" onClick={() => setShowForm(false)}>
                Cancel
              </button>
              <button className="btn-save" onClick={handleSave}>
                {editingId ? 'Update' : 'Create'} Schedule
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="schedules-grid">
        {schedules.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">📅</div>
            <div className="empty-title">No Schedules Yet</div>
            <div className="empty-desc">Create a maintenance schedule to automatically patch servers on a regular basis</div>
            <button className="btn-create" onClick={() => setShowForm(true)}>
              Create First Schedule
            </button>
          </div>
        ) : (
          schedules.map(schedule => (
            <div key={schedule.id} className={`schedule-card ${schedule.is_active ? 'active' : 'inactive'}`}>
              <div className="card-header">
                <div>
                  <h3>{schedule.name}</h3>
                  {schedule.description && <p className="description">{schedule.description}</p>}
                </div>
                <label className="toggle-switch">
                  <input
                    type="checkbox"
                    checked={schedule.is_active}
                    onChange={() => handleToggleActive(schedule)}
                  />
                  <span className="toggle-label">{schedule.is_active ? 'Active' : 'Inactive'}</span>
                </label>
              </div>

              <div className="card-details">
                <div className="detail-row">
                  <span className="label">Schedule:</span>
                  <span className="value">{getCronDescription(schedule.cron_expression)}</span>
                </div>
                <div className="detail-row">
                  <span className="label">Timezone:</span>
                  <span className="value">{schedule.timezone}</span>
                </div>
                <div className="detail-row">
                  <span className="label">Action:</span>
                  <span className="value">{schedule.action || 'Installs all pending updates'}</span>
                </div>
                <div className="detail-row">
                  <span className="label">Last run:</span>
                  <span className="value">
                    {schedule.last_run
                      ? `${new Date(schedule.last_run.at).toLocaleString()} · ${
                          schedule.last_run.result === 'started' ? 'job started' :
                          schedule.last_run.result === 'skipped' ? `skipped: ${schedule.last_run.detail}` :
                          `failed: ${schedule.last_run.detail}`}`
                      : 'Not run yet'}
                  </span>
                </div>
                <div className="detail-row">
                  <span className="label">Servers:</span>
                  <span className="value">{schedule.server_ids?.length || 0} servers</span>
                </div>
              </div>

              <div className="card-actions">
                <button className="btn-edit" onClick={() => handleEdit(schedule)}>
                  ✎ Edit
                </button>
                <button className="btn-delete" onClick={() => handleDelete(schedule.id)}>
                  ✕ Delete
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
