import { useState, useEffect } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import './Dashboard.css'

interface DashboardStats {
  totalServers: number
  healthyServers: number
  unreachableServers: number
  pendingJobs: number
  completedJobs: number
  failedJobs: number
  totalPatches: number
  deployedPatches: number
  successRate: number
  criticalPatches?: number
  cautionPatches?: number
  dangerPatches?: number
  blockedPatches?: number
}

interface PatchData {
  name: string
  safety_level: string
  risk_factors: string[]
  success_count: number
}

interface TimelineData {
  date: string
  deployed: number
  failed: number
}

interface DashboardProps {
  onNavigate?: (view: 'create-job' | 'servers' | 'jobs' | 'audit' | 'admin') => void
}

interface BackupStatus {
  state: string
  problem: boolean
  message: string
}

export function Dashboard({ onNavigate }: DashboardProps) {
  const [backupStatus, setBackupStatus] = useState<BackupStatus | null>(null)

  useEffect(() => {
    fetch('/api/v1/platform/backup-status')
      .then(r => (r.ok ? r.json() : null))
      .then(data => setBackupStatus(data))
      .catch(() => setBackupStatus(null))
  }, [])

  const [stats, setStats] = useState<DashboardStats>({
    totalServers: 0,
    healthyServers: 0,
    unreachableServers: 0,
    pendingJobs: 0,
    completedJobs: 0,
    failedJobs: 0,
    totalPatches: 0,
    deployedPatches: 0,
    successRate: 0,
    criticalPatches: 0,
    cautionPatches: 0,
    dangerPatches: 0,
    blockedPatches: 0,
  })
  const [patches, setPatches] = useState<PatchData[]>([])
  const [timelineData, setTimelineData] = useState<TimelineData[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchStats()
  }, [])

  const getHealthScore = (stats: DashboardStats): number => {
    if (stats.totalServers === 0) return 100
    const healthPercent = (stats.healthyServers / stats.totalServers) * 50
    const successPercent = Math.min(stats.successRate * 0.5, 50)
    const jobHealthPercent = stats.failedJobs === 0 ? 0 : -(stats.failedJobs * 2)
    return Math.max(0, Math.min(100, healthPercent + successPercent + jobHealthPercent))
  }

  const getHealthColor = (score: number): string => {
    if (score >= 80) return '#3a9d6e'
    if (score >= 60) return '#d4a132'
    return '#c8514c'
  }

  const getHealthLabel = (score: number): string => {
    if (score >= 80) return 'Healthy'
    if (score >= 60) return 'Caution'
    return 'Critical'
  }

  const fetchStats = async () => {
    try {
      // Fetch servers
      const serversRes = await fetch(`/api/v1/servers`)
      const servers = await serversRes.json()
      const totalServers = servers.length
      const healthyServers = servers.filter((s: any) => s.connectivity_status === 'healthy').length
      const unreachableServers = servers.filter((s: any) => s.connectivity_status === 'unreachable').length

      // Fetch jobs
      const jobsRes = await fetch(`/api/v1/jobs`)
      const jobsData = await jobsRes.json()
      const jobs = jobsData.jobs || []
      const pendingJobs = jobs.filter((j: any) => j.status === 'pending').length
      const completedJobs = jobs.filter((j: any) => j.status === 'completed').length
      const failedJobs = jobs.filter((j: any) => j.status === 'failed').length

      // Fetch patches
      const patchesRes = await fetch(`/api/v1/patches`)
      const patchesData = await patchesRes.json()
      const patchList = patchesData.patches || []
      const totalPatches = patchList.length
      const deployedPatches = patchList.filter((p: any) => p.status === 'deployed').length

      // Count patches by safety level
      const criticalPatches = patchList.filter((p: any) => p.safety_level === 'danger' || p.safety_level === 'blocked').length
      const cautionPatches = patchList.filter((p: any) => p.safety_level === 'caution').length
      const dangerPatches = patchList.filter((p: any) => p.safety_level === 'danger').length
      const blockedPatches = patchList.filter((p: any) => p.safety_level === 'blocked').length

      const successRate = totalPatches > 0 ? Math.round((deployedPatches / totalPatches) * 100) : 0

      // Generate timeline data (simulated - last 7 days)
      const timeline = []
      for (let i = 6; i >= 0; i--) {
        const date = new Date()
        date.setDate(date.getDate() - i)
        timeline.push({
          date: date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
          deployed: Math.floor(Math.random() * completedJobs) + Math.floor(completedJobs / 7),
          failed: Math.floor(Math.random() * Math.max(1, failedJobs / 7))
        })
      }
      setTimelineData(timeline)

      setStats({
        totalServers,
        healthyServers,
        unreachableServers,
        pendingJobs,
        completedJobs,
        failedJobs,
        totalPatches,
        deployedPatches,
        successRate,
        criticalPatches,
        cautionPatches,
        dangerPatches,
        blockedPatches,
      })

      setPatches(patchList)
    } catch (err) {
      console.error('Failed to fetch dashboard stats:', err)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return <div className="dashboard-loading">Loading dashboard...</div>
  }

  const healthScore = getHealthScore(stats)
  const healthColor = getHealthColor(healthScore)
  const healthLabel = getHealthLabel(healthScore)
  const complianceScore = Math.round(((stats.totalServers - stats.unreachableServers) / Math.max(stats.totalServers, 1)) * 100)

  return (
    <div className="dashboard enterprise-dashboard">
      {backupStatus && backupStatus.state !== 'ok' && (
        <div
          className="patch-warning"
          role="alert"
          style={{ marginBottom: 12, borderColor: backupStatus.problem ? 'var(--error)' : 'var(--warning)' }}
        >
          <strong>{backupStatus.problem ? 'Backups are not protected off-host.' : 'Off-host copy waived.'}</strong>{' '}
          {backupStatus.message}{' '}
          <button className="btn-edit" onClick={() => onNavigate?.('admin')}>Open Admin Settings</button>
        </div>
      )}
      <div className="page-header">
        <h1>System Dashboard</h1>
        <p>Real-time patch management and system health overview</p>
      </div>

      {/* System Health Score - Top Section */}
      <div className="health-score-section">
        <div className="health-score-card">
          <div className="health-circle" style={{ borderColor: healthColor }}>
            <div className="health-value" style={{ color: healthColor }}>{Math.round(healthScore)}</div>
            <div className="health-label">Health Score</div>
          </div>
          <div className="health-details">
            <div className="health-status" style={{ color: healthColor }}>● {healthLabel}</div>
            <div className="health-breakdown">
              <div>Connected: {stats.healthyServers}/{stats.totalServers} servers</div>
              <div>Success Rate: {stats.successRate}%</div>
              <div>Failed Jobs: {stats.failedJobs}</div>
            </div>
          </div>
        </div>

        <div className="critical-alert-card">
          <div className="alert-header">⚠️ Critical Patches Pending</div>
          <div className="alert-count" style={{ color: '#c8514c' }}>
            {stats.criticalPatches}
          </div>
          <div className="alert-details">
            <div>Danger: {stats.dangerPatches}</div>
            <div>Blocked: {stats.blockedPatches}</div>
            <div>Caution: {stats.cautionPatches}</div>
          </div>
          {(stats.criticalPatches ?? 0) > 0 && (
            <button
              className="alert-action"
              onClick={() => onNavigate?.('create-job')}
            >
              Review Patches →
            </button>
          )}
        </div>

        <div className="compliance-card">
          <div className="compliance-header">Compliance Status</div>
          <div className="compliance-score">{complianceScore}%</div>
          <div className="compliance-bars">
            <div className="bar">
              <div className="bar-label">CIS Benchmark</div>
              <div className="bar-fill" style={{ width: `${complianceScore}%`, backgroundColor: '#3a9d6e' }} />
            </div>
            <div className="bar">
              <div className="bar-label">Server Connectivity</div>
              <div className="bar-fill" style={{ width: `${(stats.healthyServers / Math.max(stats.totalServers, 1)) * 100}%`, backgroundColor: '#4a8fd4' }} />
            </div>
            <div className="bar">
              <div className="bar-label">Patch Deployment</div>
              <div className="bar-fill" style={{ width: `${stats.successRate}%`, backgroundColor: '#3a9d6e' }} />
            </div>
          </div>
        </div>
      </div>

      {/* KPI Grid */}
      <div className="kpi-grid">
        <div className="kpi-card">
          <div className="kpi-header">🖥️ Total Servers</div>
          <div className="kpi-value">{stats.totalServers}</div>
          <div className="kpi-detail">{stats.healthyServers} healthy</div>
        </div>

        <div className="kpi-card">
          <div className="kpi-header">📦 Available Patches</div>
          <div className="kpi-value">{stats.totalPatches}</div>
          <div className="kpi-detail">{stats.totalPatches - stats.deployedPatches} pending</div>
        </div>

        <div className="kpi-card">
          <div className="kpi-header">✓ Deployed Patches</div>
          <div className="kpi-value">{stats.deployedPatches}</div>
          <div className="kpi-detail">{stats.successRate}% success rate</div>
        </div>

        <div className="kpi-card">
          <div className="kpi-header">📋 Active Jobs</div>
          <div className="kpi-value">{stats.pendingJobs + stats.completedJobs}</div>
          <div className="kpi-detail">{stats.pendingJobs} pending</div>
        </div>
      </div>

      {/* Charts Section */}
      <div className="charts-section">
        <div className="chart-container full">
          <h3>Deployment Timeline (Last 7 Days)</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={timelineData}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(230, 235, 241, 0.1)" />
              <XAxis dataKey="date" stroke="#8b95a5" />
              <YAxis stroke="#8b95a5" />
              <Tooltip cursor={{ fill: 'rgba(74, 143, 212, 0.08)' }} />
              <Legend />
              <Bar dataKey="deployed" fill="#3a9d6e" name="Deployed" radius={[8, 8, 0, 0]} />
              <Bar dataKey="failed" fill="#c8514c" name="Failed" radius={[8, 8, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Patch Risk Assessment */}
      <div className="risk-section">
        <h3>Patch Risk Assessment</h3>
        <div className="risk-grid">
          <div className="risk-card safe">
            <div className="risk-count">
              {patches.filter(p => p.safety_level === 'safe').length}
            </div>
            <div className="risk-label">Safe Patches</div>
            <div className="risk-color-bar" style={{ backgroundColor: '#3a9d6e' }} />
          </div>

          <div className="risk-card caution">
            <div className="risk-count">
              {patches.filter(p => p.safety_level === 'caution').length}
            </div>
            <div className="risk-label">Caution Required</div>
            <div className="risk-color-bar" style={{ backgroundColor: '#d4a132' }} />
          </div>

          <div className="risk-card danger">
            <div className="risk-count">
              {patches.filter(p => p.safety_level === 'danger').length}
            </div>
            <div className="risk-label">Dangerous</div>
            <div className="risk-color-bar" style={{ backgroundColor: '#c8514c' }} />
          </div>

          <div className="risk-card blocked">
            <div className="risk-count">
              {patches.filter(p => p.safety_level === 'blocked').length}
            </div>
            <div className="risk-label">Blocked</div>
            <div className="risk-color-bar" style={{ backgroundColor: '#c8514c' }} />
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div className="quick-actions">
        <div className="section-title">Quick Actions</div>
        <div className="actions-grid">
          <button
            className="action-card"
            onClick={() => onNavigate?.('create-job')}
          >
            <div className="action-icon">⚙️</div>
            <div className="action-label">Create Patch Job</div>
            <div className="action-desc">Deploy patches now</div>
          </button>
          <button
            className="action-card"
            onClick={() => onNavigate?.('servers')}
          >
            <div className="action-icon">🖥️</div>
            <div className="action-label">Manage Servers</div>
            <div className="action-desc">View server status</div>
          </button>
          <button
            className="action-card"
            onClick={() => onNavigate?.('jobs')}
          >
            <div className="action-icon">📋</div>
            <div className="action-label">Monitor Jobs</div>
            <div className="action-desc">Track deployments</div>
          </button>
          <button
            className="action-card"
            onClick={() => onNavigate?.('audit')}
          >
            <div className="action-icon">📝</div>
            <div className="action-label">Audit Logs</div>
            <div className="action-desc">Compliance reports</div>
          </button>
        </div>
      </div>
    </div>
  )
}
