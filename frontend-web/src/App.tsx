import { useEffect, useState } from 'react'
import './App.css'
import { JobMonitor } from './components/JobMonitor'
import { JobList } from './components/JobList'
import { JobCreate } from './components/JobCreate'
import { ServerManagement } from './components/ServerManagement'
import { PatchHistory } from './components/PatchHistory'
import { AuditLog } from './components/AuditLog'
import { Dashboard } from './components/Dashboard'
import { PatchGroups } from './components/PatchGroups'
import { AdminSettings } from './components/AdminSettings'
import { Account } from './components/Account'
import { RiskDashboard } from './components/RiskDashboard'
import { clearSession, getUser, onSessionChange, type SessionUser } from './lib/auth'
import { ComplianceReporting } from './components/ComplianceReporting'
import { loadDisplayTimezone, useDisplayTimezone } from './lib/displayTime'
import { Schedules } from './components/Schedules'
import { DistroUpgrades } from './components/DistroUpgrades'
import { ServerDetail } from './components/ServerDetail'

type ViewType = 'dashboard' | 'servers' | 'server-detail' | 'jobs' | 'patches' | 'schedules' | 'distro-upgrades' | 'audit' | 'create-job' | 'patch-groups' | 'admin' | 'risk-assessment' | 'compliance' | 'account'

function useSessionUser(): SessionUser | null {
  const [user, setUser] = useState<SessionUser | null>(getUser())
  useEffect(() => onSessionChange(() => setUser(getUser())), [])
  return user
}

interface MenuItem {
  id: ViewType
  label: string
  icon: string
  section: 'work' | 'monitoring'
}

const menuItems: MenuItem[] = [
  { id: 'dashboard', label: 'Dashboard', icon: '📊', section: 'work' },
  { id: 'servers', label: 'Servers', icon: '🖥️', section: 'work' },
  { id: 'patch-groups', label: 'Patch Groups', icon: '📦', section: 'work' },
  { id: 'create-job', label: 'Create Job', icon: '⚙️', section: 'work' },
  { id: 'distro-upgrades', label: 'Distro Upgrades', icon: '⬆️', section: 'work' },
  { id: 'jobs', label: 'Jobs', icon: '📋', section: 'work' },
  { id: 'risk-assessment', label: 'Risk & Compliance', icon: '🔍', section: 'monitoring' },
  { id: 'compliance', label: 'Compliance Reports', icon: '📋', section: 'monitoring' },
  { id: 'patches', label: 'Patches', icon: '📦', section: 'monitoring' },
  { id: 'schedules', label: 'Schedules', icon: '⏱️', section: 'monitoring' },
  { id: 'audit', label: 'Audit Logs', icon: '📝', section: 'monitoring' },
  { id: 'admin', label: 'Admin Settings', icon: '⚙️', section: 'monitoring' },
]

function App() {
  const theme = 'dark'
  const currentUser = useSessionUser()
  const isAdmin = (currentUser?.level ?? 0) >= 3
  const handleSignOut = () => clearSession()
  const [currentView, setCurrentView] = useState<ViewType>('dashboard')
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [selectedServerId, setSelectedServerId] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(true)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  useDisplayTimezone()
  useEffect(() => {
    loadDisplayTimezone()
  }, [])

  return (
    <div className="app-layout" data-theme={theme}>
      {/* Sidebar Navigation */}
      <aside className={`sidebar ${sidebarOpen ? 'open' : 'closed'}`}>
        <div className="sidebar-header">
          <div className="logo" role="img" aria-label="JÁRN, forged patching">
            <svg className="logo-icon" viewBox="0 0 40 40" aria-hidden="true">
              <defs>
                <linearGradient id="jarn-steel" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stopColor="#ffffff" />
                  <stop offset="0.5" stopColor="#c9d2dd" />
                  <stop offset="1" stopColor="#7d8a9b" />
                </linearGradient>
              </defs>
              <path
                className="logo-anvil"
                d="M4 9h32v6h-8v5h4l3 7H10l3-7h7v-5H4z"
                fill="url(#jarn-steel)"
              />
              <rect x="9" y="30" width="22" height="4" rx="1" fill="#4a8fd4" />
              <circle className="logo-spark logo-spark-1" cx="31" cy="5" r="1.6" />
              <circle className="logo-spark logo-spark-2" cx="36" cy="10" r="1.1" />
              <circle className="logo-spark logo-spark-3" cx="27" cy="2" r="1" />
            </svg>
            <div className="logo-wordmark">
              <span className="logo-text" aria-hidden="true">
                J<span className="logo-accent">Á</span>RN
              </span>
              <span className="logo-tagline" aria-hidden="true">Forged patching</span>
            </div>
          </div>
          <button
            className="sidebar-toggle"
            onClick={() => setSidebarOpen(!sidebarOpen)}
            title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
          >
            {sidebarOpen ? '◀' : '▶'}
          </button>
        </div>

        <nav className="sidebar-nav">
          <div className="nav-section">
            <h3 className="nav-section-title">WORK</h3>
            {menuItems
              .filter(item => item.section === 'work')
              .map(item => (
                <button
                  key={item.id}
                  className={`nav-item ${currentView === item.id ? 'active' : ''}`}
                  onClick={() => {
                    setCurrentView(item.id)
                    setSelectedJobId(null)
                  }}
                  title={item.label}
                >
                  <span className="nav-icon">{item.icon}</span>
                  {sidebarOpen && <span className="nav-label">{item.label}</span>}
                </button>
              ))}
          </div>

          <div className="nav-section">
            <h3 className="nav-section-title">MONITORING</h3>
            {menuItems
              .filter(item => item.section === 'monitoring' && (item.id !== 'admin' || isAdmin))
              .map(item => (
                <button
                  key={item.id}
                  className={`nav-item ${currentView === item.id ? 'active' : ''}`}
                  onClick={() => {
                    setCurrentView(item.id)
                    setSelectedJobId(null)
                  }}
                  title={item.label}
                >
                  <span className="nav-icon">{item.icon}</span>
                  {sidebarOpen && <span className="nav-label">{item.label}</span>}
                </button>
              ))}
          </div>
        </nav>

        <div className="sidebar-footer">
          {currentUser && (
            <div className="signed-in-user">
              <button className="signed-in-name" onClick={() => setCurrentView('account')} title="Account and MFA">{currentUser.username}</button>
              <div className="signed-in-role">{currentUser.roles.join(', ') || 'no role'}</div>
              <button className="btn-cancel" onClick={handleSignOut}>Sign out</button>
            </div>
          )}
        </div>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        <div className="content-wrapper">
          {currentView === 'dashboard' && (
            <Dashboard
              onNavigate={(view) => {
                setCurrentView(view)
                setSelectedJobId(null)
              }}
            />
          )}

          {currentView === 'servers' && (
            <div className="page-container">
              <div className="page-header">
                <h1>Server Inventory</h1>
                <p>Manage your Linux servers</p>
              </div>
              <ServerManagement onOpenServer={(id) => {
                setSelectedServerId(id)
                setCurrentView('server-detail')
              }} />
            </div>
          )}

          {currentView === 'server-detail' && selectedServerId && (
            <ServerDetail serverId={selectedServerId} onBack={() => setCurrentView('servers')} />
          )}

          {currentView === 'patch-groups' && (
            <div className="page-container">
              <div className="page-header">
                <h1>Patch Groups</h1>
                <p>Manage patch groups with change management tracking</p>
              </div>
              <PatchGroups />
            </div>
          )}

          {currentView === 'create-job' && (
            <div className="page-container">
              <div className="page-header">
                <h1>Create Patch Job</h1>
                <p>Deploy patches to your infrastructure</p>
              </div>
              <JobCreate
                onJobCreated={() => {
                  setCurrentView('jobs')
                }}
              />
            </div>
          )}

          {currentView === 'jobs' && (
            <>
              {selectedJobId ? (
                <div className="page-container">
                  <button
                    className="back-button"
                    onClick={() => setSelectedJobId(null)}
                  >
                    ← Back to Jobs
                  </button>
                  <div className="page-header">
                    <h1>Job Monitor</h1>
                  </div>
                  <JobMonitor jobId={selectedJobId} />
                </div>
              ) : (
                <div className="page-container">
                  <div className="page-header">
                    <h1>Patch Jobs</h1>
                    <p>View and manage your patch deployment jobs</p>
                  </div>
                  <JobList onJobSelected={setSelectedJobId} />
                </div>
              )}
            </>
          )}

          {currentView === 'risk-assessment' && (
            <RiskDashboard onNavigate={(view) => setCurrentView(view as ViewType)} />
          )}

          {currentView === 'patches' && (
            <div className="page-container">
              <div className="page-header">
                <h1>Patch History</h1>
                <p>View all deployed patches and deployment history</p>
              </div>
              <PatchHistory />
            </div>
          )}

          {currentView === 'schedules' && (
            <Schedules />
          )}

          {currentView === 'distro-upgrades' && (
            <DistroUpgrades />
          )}

          {currentView === 'audit' && (
            <div className="page-container">
              <AuditLog />
            </div>
          )}

          {currentView === 'admin' && (
            <AdminSettings />
          )}

          {currentView === 'account' && <Account />}
          {currentView === 'compliance' && (
            <ComplianceReporting />
          )}
        </div>
      </main>
    </div>
  )
}

export default App
