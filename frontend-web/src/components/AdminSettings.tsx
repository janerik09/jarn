import { useEffect, useState, ReactNode } from 'react'
import './SsoSettings.css'
import './AdminSettings.css'
import { setCachedDefaultPageSize } from '../lib/defaultPageSize'
import { getUser } from '../lib/auth'
import { BackupMappings } from './BackupMappings'
import { listTimezones, setDisplayTimezone } from '../lib/displayTime'

interface PatchingConfig {
  max_concurrent_servers: number
  pre_patch_health_check: boolean
  post_patch_validation: boolean
  automatic_rollback_on_failure: boolean
  max_downtime_minutes: number
}

interface BackupTarget {
  id: string
  name: string
  provider: string
  api_url: string | null
  token_id: string | null
  storage: string | null
  verify_tls: boolean
  token_secret_set: boolean
  token_secret?: string
}

interface BackupSettings {
  enabled: boolean
  targets: BackupTarget[]
}

interface Settings {
  general: {
    default_page_size: number
    scan_interval_hours: number
    timezone: string
    kubernetes_control_server_id: string | null
  }
  backup: BackupSettings
  patching_config: PatchingConfig | null
  auto_approve_critical: boolean
  require_approval_for_kernel: boolean
  maintenance_window_start: string | null
  maintenance_window_end: string | null
}

type Update = (patch: Partial<Settings>) => void

interface SectionDef {
  id: string
  title: string
  description: string
  render: (settings: Settings, update: Update) => ReactNode
}

const apiUrl = () => `/api/v1/admin/settings`

// Add a new settings group by appending an entry here.
const SECTIONS: SectionDef[] = [
  {
    id: 'display',
    title: 'Display',
    description: 'How lists and tables appear across the application.',
    render: (s, update) => (
      <div className="settings-field">
        <label htmlFor="default-page-size">Default rows per page</label>
        <select
          id="default-page-size"
          value={s.general.default_page_size}
          onChange={(e) => update({ general: { ...s.general, default_page_size: Number(e.target.value) } })}
        >
          <option value={10}>10</option>
          <option value={25}>25</option>
          <option value={50}>50</option>
          <option value={100}>100</option>
        </select>
        <p className="settings-help">
          Applies to Servers, Jobs, Patch History, Audit Logs and Distro Upgrades. Users can still change the
          page size from the pagination bar for their current view.
        </p>
        <label htmlFor="display-timezone">Time zone</label>
        <select
          id="display-timezone"
          value={s.general.timezone}
          onChange={(e) => update({ general: { ...s.general, timezone: e.target.value } })}
        >
          {listTimezones().map(zone => (
            <option key={zone} value={zone}>{zone}</option>
          ))}
        </select>
        <p className="settings-help">
          Every time shown in the application uses this zone. Defaults to UTC.
        </p>
        <label htmlFor="scan-interval">Rescan servers when the last scan is older than</label>
        <select
          id="scan-interval"
          value={s.general.scan_interval_hours}
          onChange={(e) => update({ general: { ...s.general, scan_interval_hours: Number(e.target.value) } })}
        >
          <option value={0}>Never (scan only after patch and distro jobs)</option>
          <option value={1}>1 hour</option>
          <option value={6}>6 hours</option>
          <option value={12}>12 hours</option>
          <option value={24}>24 hours</option>
        </select>
        <p className="settings-help">
          Checks run hourly. Each check logs in to the servers that are due, so shorter intervals add SSH load.
        </p>
      </div>
    ),
  },
  {
    id: 'notifications',
    title: 'Notifications',
    description: 'Email and webhook alerts for finished or failed jobs, skipped scheduled runs, platform problems and certificate expiry.',
    render: () => <NotificationsSection />,
  },
  {
    id: 'users',
    title: 'Users',
    description: 'Who can sign in, and what each person is allowed to do.',
    render: () => <UsersSection />,
  },
  {
    id: 'web',
    title: 'Web access',
    description: 'How the dashboard is reached. HTTP is the default. Enable HTTPS with a generated certificate or your own.',
    render: () => <WebAccessSection />,
  },
  {
    id: 'backup',
    title: 'Backup',
    description: 'Back up servers before patching and restore them if a patch cannot be rolled back. Proxmox VE with a Proxmox Backup Server storage is supported.',
    render: (s, update) => <BackupSection settings={s} update={update} />,
  },
  {
    id: 'sso',
    title: 'Single sign-on',
    description: 'Sign in with Microsoft Entra ID, Okta, Google, Keycloak or another OpenID Connect provider, or with Active Directory / LDAP. Roles come from the groups each user belongs to.',
    render: () => <SsoSection />,
  },
  {
    id: 'hostkeys',
    title: 'Server host keys',
    description: 'JÁRN only connects to a server with the SSH host key you approved for it. Approve each key after comparing its fingerprint with the server.',
    render: () => <HostKeysSection />,
  },
  {
    id: 'offsite',
    title: 'Off-host copy',
    description: 'Send copies of the database backups and audit archives to another server over SFTP, so a copy survives loss of this host. Off until you turn it on.',
    render: () => <OffsiteSection />,
  },
  {
    id: 'kubernetes',
    title: 'Kubernetes',
    description: 'Kubernetes nodes in a patch job are drained, patched and checked one at a time. Drains and readiness checks run from a control host that has cluster-admin access.',
    render: (s, update) => <KubernetesSection settings={s} update={update} />,
  },
  {
    id: 'patching',
    title: 'Patching policy',
    description: 'Approval and maintenance-window settings. Values are saved for reference and are not yet enforced by the job runner.',
    render: (s, update) => (
      <>
        <div className="settings-field">
          <label className="settings-toggle">
            <input
              type="checkbox"
              checked={s.auto_approve_critical}
              onChange={(e) => update({ auto_approve_critical: e.target.checked })}
            />
            <span>Auto-approve critical security patches</span>
          </label>
        </div>
        <div className="settings-field">
          <label className="settings-toggle">
            <input
              type="checkbox"
              checked={s.require_approval_for_kernel}
              onChange={(e) => update({ require_approval_for_kernel: e.target.checked })}
            />
            <span>Require approval for kernel updates</span>
          </label>
        </div>
        <div className="settings-row">
          <div className="settings-field">
            <label htmlFor="window-start">Maintenance window start (UTC)</label>
            <input
              id="window-start"
              type="time"
              value={s.maintenance_window_start || ''}
              onChange={(e) => update({ maintenance_window_start: e.target.value })}
            />
          </div>
          <div className="settings-field">
            <label htmlFor="window-end">Maintenance window end (UTC)</label>
            <input
              id="window-end"
              type="time"
              value={s.maintenance_window_end || ''}
              onChange={(e) => update({ maintenance_window_end: e.target.value })}
            />
          </div>
        </div>
      </>
    ),
  },
]

interface TlsStatus {
  mode: 'http' | 'self_signed' | 'custom'
  redirect_https: boolean
  hostnames: string[]
  ips: string[]
  self_signed_available: boolean
  custom_available: boolean
  certificate: {
    subject: string
    issuer: string
    self_signed: boolean
    sans: string[]
    not_after: string
    days_left: number
  } | null
  applied: { mode: string; ok: boolean; error: string; applied_at: string } | null
  urls: { http: string; https: string }
}

const MODE_OPTIONS: { value: TlsStatus['mode']; title: string; text: string }[] = [
  { value: 'http', title: 'HTTP only (default)', text: 'The dashboard is served over plain HTTP on port 80.' },
  { value: 'self_signed', title: 'HTTPS with a generated certificate', text: 'The app creates a self-signed certificate. Browsers will warn unless you import it.' },
  { value: 'custom', title: 'HTTPS with my own certificate', text: 'Use a certificate from your CA or Let\'s Encrypt. Upload it below.' },
]

function WebAccessSection() {
  const [status, setStatus] = useState<TlsStatus | null>(null)
  const [mode, setMode] = useState<TlsStatus['mode']>('http')
  const [redirect, setRedirect] = useState(false)
  const [hostnames, setHostnames] = useState('')
  const [ips, setIps] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [certFile, setCertFile] = useState<File | null>(null)
  const [keyFile, setKeyFile] = useState<File | null>(null)

  const load = async () => {
    try {
      const res = await fetch('/api/v1/admin/tls')
      const data: TlsStatus = await res.json()
      setStatus(data)
      setMode(data.mode)
      setRedirect(data.redirect_https)
      const host = window.location.hostname
      setHostnames((data.hostnames.length ? data.hostnames : [host]).join(', '))
      setIps(data.ips.join(', '))
    } catch {
      setMessage({ ok: false, text: 'Could not load web access settings' })
    }
  }

  useEffect(() => { load() }, [])

  const splitList = (text: string) => text.split(/[\s,]+/).map(v => v.trim()).filter(Boolean)

  const apply = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const res = await fetch('/api/v1/admin/tls', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode,
          redirect_https: mode === 'http' ? false : redirect,
          hostnames: mode === 'self_signed' ? splitList(hostnames) : (status?.hostnames ?? []),
          ips: mode === 'self_signed' ? splitList(ips) : (status?.ips ?? []),
        }),
      })
      const data = await res.json()
      if (!res.ok) {
        setMessage({ ok: false, text: data.detail || 'Could not apply the setting' })
        return
      }
      setStatus(data)
      setMessage({ ok: true, text: 'Saved. The web server applies the change within a few seconds.' })
    } catch {
      setMessage({ ok: false, text: 'Could not reach the backend' })
    } finally {
      setBusy(false)
    }
  }

  const uploadCertificate = async () => {
    if (!certFile || !keyFile) {
      setMessage({ ok: false, text: 'Choose the certificate file and the private key file' })
      return
    }
    setBusy(true)
    setMessage(null)
    try {
      const form = new FormData()
      form.append('certificate', certFile)
      form.append('private_key', keyFile)
      const res = await fetch('/api/v1/admin/tls/certificate', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) {
        setMessage({ ok: false, text: data.detail || 'Upload failed' })
        return
      }
      setStatus(data)
      setMode('custom')
      setCertFile(null)
      setKeyFile(null)
      setMessage({ ok: true, text: 'Certificate uploaded and selected. The web server applies it within a few seconds.' })
    } catch {
      setMessage({ ok: false, text: 'Could not reach the backend' })
    } finally {
      setBusy(false)
    }
  }

  if (!status) return <p className="settings-help">Loading web access settings…</p>

  const httpsSelected = mode !== 'http'
  const cert = status.certificate
  const expiring = cert ? cert.days_left < 30 : false

  return (
    <div>
      <div className="settings-card">
        <h3>Access mode</h3>
        {MODE_OPTIONS.map(option => {
          const disabled = option.value === 'custom' && !status.custom_available && mode !== 'custom'
          return (
            <label key={option.value} className="settings-toggle" style={{ alignItems: 'flex-start', opacity: disabled ? 0.5 : 1 }}>
              <input
                type="radio"
                name="tls-mode"
                checked={mode === option.value}
                disabled={disabled}
                onChange={() => setMode(option.value)}
              />
              <span>
                <strong>{option.title}</strong>
                <br />
                <span className="settings-help">{option.text}</span>
              </span>
            </label>
          )
        })}
        {httpsSelected && (
          <label className="settings-toggle">
            <input type="checkbox" checked={redirect} onChange={(e) => setRedirect(e.target.checked)} />
            Redirect HTTP to HTTPS
          </label>
        )}
      </div>

      {mode === 'self_signed' && (
        <div className="settings-card">
          <h3>Generated certificate</h3>
          <div className="settings-field">
            <label>Hostnames (comma separated)</label>
            <input type="text" value={hostnames} onChange={(e) => setHostnames(e.target.value)} />
          </div>
          <div className="settings-field">
            <label>IP addresses (comma separated)</label>
            <input type="text" value={ips} onChange={(e) => setIps(e.target.value)} placeholder="192.0.2.202" />
          </div>
          <p className="settings-help">
            The certificate is valid for 397 days. It is regenerated when these names change. Import it on each
            computer that opens the dashboard, or the browser will show a warning.
          </p>
          <button className="btn-edit" onClick={async () => {
            const res = await fetch('/api/v1/admin/tls/certificate')
            if (!res.ok) return
            const url = URL.createObjectURL(await res.blob())
            const link = document.createElement('a')
            link.href = url
            link.download = 'jarn-server.crt'
            link.click()
            URL.revokeObjectURL(url)
          }}>Download the certificate</button>
        </div>
      )}

      {mode === 'custom' && (
        <div className="settings-card">
          <h3>Your certificate</h3>
          <p className="settings-help">
            Upload the certificate as PEM. If your CA gave you an intermediate chain, put it in the same file below the
            server certificate. The private key must be PEM and must not be password protected.
          </p>
          <div className="settings-field">
            <label>Certificate (PEM)</label>
            <input type="file" accept=".pem,.crt,.cer" onChange={(e) => setCertFile(e.target.files?.[0] ?? null)} />
          </div>
          <div className="settings-field">
            <label>Private key (PEM)</label>
            <input type="file" accept=".pem,.key" onChange={(e) => setKeyFile(e.target.files?.[0] ?? null)} />
          </div>
          <button className="btn-save-settings" onClick={uploadCertificate} disabled={busy || !certFile || !keyFile}>
            Upload and use this certificate
          </button>
        </div>
      )}

      {cert && (
        <div className="settings-card">
          <h3>Active certificate</h3>
          <div className="settings-help">Subject: {cert.subject}</div>
          <div className="settings-help">Issuer: {cert.issuer}{cert.self_signed ? ' (self-signed)' : ''}</div>
          <div className="settings-help">Names: {cert.sans.join(', ') || 'none'}</div>
          <div className="settings-help" style={{ color: expiring ? 'var(--warning)' : undefined }}>
            Expires: {new Date(cert.not_after).toLocaleDateString()} ({cert.days_left} days left)
          </div>
        </div>
      )}

      <div className="settings-card">
        <h3>Status</h3>
        {status.applied ? (
          <p className="settings-help" style={{ color: status.applied.ok ? undefined : 'var(--error)' }}>
            {status.applied.ok
              ? `Serving ${status.applied.mode === 'http' ? 'HTTP' : 'HTTPS'} (applied ${new Date(status.applied.applied_at).toLocaleString()})`
              : `Last change was rejected and the previous setting is still running: ${status.applied.error}`}
          </p>
        ) : (
          <p className="settings-help">Not applied yet.</p>
        )}
        <p className="settings-help">
          Dashboard address: <strong>{httpsSelected ? status.urls.https : status.urls.http}</strong>
        </p>
      </div>

      {message && (
        <p className="settings-help" style={{ color: message.ok ? 'var(--success)' : 'var(--error)' }}>{message.text}</p>
      )}
      <button className="btn-save-settings" onClick={apply} disabled={busy}>
        {busy ? 'Applying…' : 'Apply web access settings'}
      </button>
    </div>
  )
}

interface SmtpView {
  host: string
  port: number
  username: string
  from_address: string
  to: string
  starttls: boolean
  password_set: boolean
}

const EMAIL_PRESETS: { id: string; label: string; host: string; port: number; starttls: boolean; help: string }[] = [
  {
    id: 'custom', label: 'Custom server', host: '', port: 587, starttls: true,
    help: 'Enter the details from your mail provider.',
  },
  {
    id: 'gmail', label: 'Gmail (free)', host: 'smtp.gmail.com', port: 587, starttls: true,
    help: 'Use your full Gmail address as the username. Gmail needs an App Password: turn on 2-Step Verification, then create one at myaccount.google.com/apppasswords and paste it here. Your normal Gmail password will not work.',
  },
  {
    id: 'outlook', label: 'Outlook.com / Microsoft 365', host: 'smtp.office365.com', port: 587, starttls: true,
    help: 'Use your full address as the username. Microsoft may require an app password or OAuth; if login fails, check the account security settings for SMTP access.',
  },
  {
    id: 'yahoo', label: 'Yahoo Mail (free)', host: 'smtp.mail.yahoo.com', port: 587, starttls: true,
    help: 'Use your full Yahoo address. Yahoo needs an app password, created under Account Security.',
  },
]

function NotificationsSection() {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [url, setUrl] = useState('')
  const [smtp, setSmtp] = useState<SmtpView | null>(null)
  const [preset, setPreset] = useState('custom')
  const [smtpPassword, setSmtpPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const load = async () => {
    try {
      const res = await fetch('/api/v1/admin/notifications')
      const data = await res.json()
      setConfigured(data.webhook_configured)
      setSmtp(data.smtp)
      const match = EMAIL_PRESETS.find(p => p.host && p.host === data.smtp?.host)
      setPreset(match ? match.id : 'custom')
    } catch {
      setMessage({ ok: false, text: 'Could not load notification settings' })
    }
  }

  useEffect(() => { load() }, [])

  const putNotifications = async (body: object, okText: string) => {
    setBusy(true)
    setMessage(null)
    try {
      const res = await fetch('/api/v1/admin/notifications', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) {
        setMessage({ ok: false, text: data.detail?.[0]?.msg || data.detail || 'Could not save' })
        return false
      }
      setConfigured(data.webhook_configured)
      setSmtp(data.smtp)
      setMessage({ ok: true, text: okText })
      return true
    } finally {
      setBusy(false)
    }
  }

  const saveWebhook = async (clear: boolean) => {
    const saved = await putNotifications({ webhook_url: clear ? null : url }, clear ? 'Webhook removed' : 'Webhook saved')
    if (saved) setUrl('')
  }

  const applyPreset = (id: string) => {
    setPreset(id)
    const p = EMAIL_PRESETS.find(x => x.id === id)
    if (p && id !== 'custom') {
      setSmtp(prev => ({ ...(prev as SmtpView), host: p.host, port: p.port, starttls: p.starttls }))
    }
  }

  const saveEmail = async (clear: boolean) => {
    if (clear) {
      await putNotifications({ smtp: { host: '' } }, 'Email settings removed')
      return
    }
    if (!smtp) return
    const saved = await putNotifications({
      smtp: {
        host: smtp.host,
        port: Number(smtp.port) || 587,
        username: smtp.username || null,
        password: smtpPassword || null,
        from_address: smtp.from_address,
        to: smtp.to,
        starttls: smtp.starttls,
      },
    }, 'Email settings saved')
    if (saved) setSmtpPassword('')
  }

  const test = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const res = await fetch('/api/v1/admin/notifications/test', { method: 'POST' })
      const data = await res.json()
      if (res.ok) {
        const parts = [data.webhook === null ? null : `webhook ${data.webhook ? 'ok' : 'failed'}`,
                       data.email === null ? null : `email ${data.email ? 'ok' : 'failed'}`].filter(Boolean)
        setMessage({ ok: true, text: `Test sent: ${parts.join(', ')}` })
      } else {
        setMessage({ ok: false, text: data.detail || 'Test failed' })
      }
    } finally {
      setBusy(false)
    }
  }

  const activePreset = EMAIL_PRESETS.find(p => p.id === preset) ?? EMAIL_PRESETS[0]
  const emailConfigured = Boolean(smtp?.host)

  return (
    <div>
      <div className="sso-header">
        <p>
          Email: {emailConfigured ? `set up through ${smtp?.host}` : 'not set up'}. Webhook: {configured ? 'set up' : 'not set up'}.
        </p>
      </div>

      <div className="sso-section">
        <h4>Email</h4>
        <p>Sent for finished or failed jobs, skipped scheduled runs, platform problems, and certificates that are about to expire.</p>
        <div className="sso-tiles" style={{ marginBottom: 10 }}>
          {EMAIL_PRESETS.map(p => (
            <button key={p.id} type="button" className="sso-tile" onClick={() => applyPreset(p.id)}
              style={{ borderColor: preset === p.id ? '#4a8fd4' : undefined }}>
              {p.label}
            </button>
          ))}
        </div>
        <p className="sso-hint" style={{ marginBottom: 12 }}>{activePreset.help}</p>
        <div className="sso-grid">
          <div>
            <label>SMTP server</label>
            <input type="text" value={smtp?.host ?? ''} onChange={(e) => setSmtp({ ...(smtp as SmtpView), host: e.target.value })} placeholder="smtp.example.com" />
          </div>
          <div>
            <label>Port</label>
            <input type="number" value={smtp?.port ?? 587} onChange={(e) => setSmtp({ ...(smtp as SmtpView), port: Number(e.target.value) })} />
          </div>
          <div className="wide sso-toggles">
            <label className="settings-toggle">
              <input type="checkbox" checked={smtp?.starttls ?? true} onChange={(e) => setSmtp({ ...(smtp as SmtpView), starttls: e.target.checked })} />
              Use STARTTLS (recommended on port 587)
            </label>
          </div>
          <div>
            <label>User name</label>
            <input type="text" value={smtp?.username ?? ''} onChange={(e) => setSmtp({ ...(smtp as SmtpView), username: e.target.value })} placeholder="you@example.com" />
          </div>
          <div>
            <label>Password or app password</label>
            <input type="password" value={smtpPassword} onChange={(e) => setSmtpPassword(e.target.value)}
              placeholder={smtp?.password_set ? 'Saved, leave blank to keep' : 'Enter the password'} />
          </div>
          <div>
            <label>From address</label>
            <input type="text" value={smtp?.from_address ?? ''} onChange={(e) => setSmtp({ ...(smtp as SmtpView), from_address: e.target.value })} placeholder="jarn@example.com" />
          </div>
          <div>
            <label>Send to</label>
            <input type="text" value={smtp?.to ?? ''} onChange={(e) => setSmtp({ ...(smtp as SmtpView), to: e.target.value })} placeholder="ops@example.com" />
            <div className="sso-hint">Separate several addresses with commas.</div>
          </div>
        </div>
        <div className="sso-actions">
          <button className="btn-save-settings" onClick={() => saveEmail(false)} disabled={busy || !smtp?.host || !smtp?.from_address || !smtp?.to}>Save email</button>
          <button className="btn-cancel" onClick={() => saveEmail(true)} disabled={busy || !emailConfigured}>Remove email</button>
        </div>
      </div>

      <div className="sso-section">
        <h4>Webhook</h4>
        <p>Posts a short message to Slack, Microsoft Teams or any endpoint that accepts incoming webhooks. The URL is stored encrypted and isn't shown again after you save it.</p>
        <div className="sso-grid">
          <div className="wide">
            <label>Webhook URL</label>
            <input type="text" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.example.com/…" />
            <div className="sso-hint">{configured === null ? 'Checking…' : configured ? 'A webhook is set up. Enter a new URL to replace it.' : 'No webhook is set up yet.'}</div>
          </div>
        </div>
        <div className="sso-actions">
          <button className="btn-save-settings" onClick={() => saveWebhook(false)} disabled={busy || !url.trim()}>Save webhook</button>
          <button className="btn-cancel" onClick={() => saveWebhook(true)} disabled={busy || !configured}>Remove webhook</button>
        </div>
      </div>

      <div className="sso-section">
        <h4>Test</h4>
        <p>Sends a test message to each channel that is set up.</p>
        <div className="sso-actions" style={{ marginTop: 0 }}>
          <button className="btn-test" onClick={test} disabled={busy || (!configured && !emailConfigured)}>Send test message</button>
        </div>
      </div>

      {message && (
        <p className="sso-message" style={{ color: message.ok ? 'var(--success)' : 'var(--error)', marginTop: 8 }}>{message.text}</p>
      )}
    </div>
  )
}

interface SsoProviderForm {
  id: string
  name: string
  preset: string
  enabled: boolean
  issuer: string
  client_id: string
  client_secret: string
  client_secret_set: boolean
  scopes: string
  username_claim: string
  groups_claim: string
  role_map: Record<string, string>
  default_role: string
  allowed_emails: string
}

interface SsoLdapForm {
  enabled: boolean
  host: string
  port: number
  use_ssl: boolean
  starttls: boolean
  verify_tls: boolean
  bind_dn: string
  bind_password: string
  bind_password_set: boolean
  base_dn: string
  user_filter: string
  username_attribute: string
  email_attribute: string
  group_attribute: string
  group_base_dn: string
  role_map: Record<string, string>
  default_role: string
}


const ROLE_OPTIONS = ['', 'admin', 'operator', 'viewer']

interface SsoProviderForm {
  id: string
  name: string
  preset: string
  enabled: boolean
  issuer: string
  client_id: string
  client_secret: string
  client_secret_set: boolean
  scopes: string
  username_claim: string
  groups_claim: string
  role_map: Record<string, string>
  default_role: string
  allowed_emails: string
}

interface SsoLdapForm {
  enabled: boolean
  host: string
  port: number
  use_ssl: boolean
  starttls: boolean
  verify_tls: boolean
  bind_dn: string
  bind_password: string
  bind_password_set: boolean
  base_dn: string
  user_filter: string
  username_attribute: string
  email_attribute: string
  group_attribute: string
  group_base_dn: string
  role_map: Record<string, string>
  default_role: string
}

interface SsoPreset { name: string; issuer_hint: string; username_claim: string; groups_claim: string; help: string }

// Group names to role. Rows are kept as a list so that editing a name never drops the row.
function RoleMapEditor({ value, onChange, placeholder }: { value: Record<string, string>; onChange: (v: Record<string, string>) => void; placeholder: string }) {
  const [rows, setRows] = useState<{ group: string; role: string }[]>(() => Object.entries(value).map(([group, role]) => ({ group, role })))
  const commit = (next: { group: string; role: string }[]) => {
    setRows(next)
    onChange(Object.fromEntries(next.filter(r => r.group.trim()).map(r => [r.group.trim(), r.role])))
  }
  return (
    <div className="sso-rolemap">
      {rows.map((row, i) => (
        <div className="sso-rolemap-row" key={i}>
          <input type="text" value={row.group} placeholder={placeholder}
            onChange={e => commit(rows.map((r, j) => (j === i ? { ...r, group: e.target.value } : r)))} />
          <select value={row.role} onChange={e => commit(rows.map((r, j) => (j === i ? { ...r, role: e.target.value } : r)))}>
            {['admin', 'operator', 'viewer'].map(r => <option key={r} value={r}>{r}</option>)}
          </select>
          <button className="btn-cancel" onClick={() => commit(rows.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <div><button className="btn-edit" onClick={() => setRows([...rows, { group: '', role: 'viewer' }])}>Add group</button></div>
    </div>
  )
}

function SsoSection() {
  const [tab, setTab] = useState<'general' | 'oidc' | 'ldap'>('general')
  const [loaded, setLoaded] = useState(false)
  const [publicUrl, setPublicUrl] = useState('')
  const [redirectUri, setRedirectUri] = useState<string | null>(null)
  const [allowLocal, setAllowLocal] = useState(true)
  const [breakGlass, setBreakGlass] = useState('')
  const [presets, setPresets] = useState<Record<string, SsoPreset>>({})
  const [providers, setProviders] = useState<SsoProviderForm[]>([])
  const [openProvider, setOpenProvider] = useState<number | null>(null)
  const [ldap, setLdap] = useState<SsoLdapForm | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [ldapTestUser, setLdapTestUser] = useState('')
  const [ldapTestResult, setLdapTestResult] = useState<string | null>(null)

  const toForm = (p: any): SsoProviderForm => ({
    id: p.id, name: p.name || p.id, preset: p.preset || 'oidc', enabled: !!p.enabled, issuer: p.issuer || '',
    client_id: p.client_id || '', client_secret: '', client_secret_set: !!p.client_secret_set,
    scopes: p.scopes || 'openid profile email', username_claim: p.username_claim || 'preferred_username',
    groups_claim: p.groups_claim ?? 'groups', role_map: p.role_map || {}, default_role: p.default_role || '',
    allowed_emails: (p.allowed_emails || []).join(', '),
  })

  const load = async () => {
    try {
      const res = await fetch('/api/v1/admin/sso')
      if (!res.ok) throw new Error()
      const d = await res.json()
      setPublicUrl(d.public_base_url || '')
      setRedirectUri(d.redirect_uri)
      setAllowLocal(d.allow_local_login)
      setBreakGlass((d.break_glass_usernames || []).join(', '))
      setPresets(d.presets || {})
      setProviders((d.providers || []).map(toForm))
      setLdap({
        enabled: !!d.ldap?.enabled, host: d.ldap?.host || '', port: d.ldap?.port || 636, use_ssl: d.ldap?.use_ssl ?? true,
        starttls: !!d.ldap?.starttls, verify_tls: d.ldap?.verify_tls ?? true, bind_dn: d.ldap?.bind_dn || '',
        bind_password: '', bind_password_set: !!d.ldap?.bind_password_set, base_dn: d.ldap?.base_dn || '',
        user_filter: d.ldap?.user_filter || '(&(objectClass=person)(uid={username}))',
        username_attribute: d.ldap?.username_attribute || 'uid', email_attribute: d.ldap?.email_attribute || 'mail',
        group_attribute: d.ldap?.group_attribute ?? 'memberOf', group_base_dn: d.ldap?.group_base_dn || '',
        role_map: d.ldap?.role_map || {}, default_role: d.ldap?.default_role || '',
      })
      setLoaded(true)
    } catch {
      setMessage({ ok: false, text: 'Could not load single sign-on settings' })
    }
  }

  useEffect(() => { load() }, [])

  const updateProvider = (index: number, patch: Partial<SsoProviderForm>) =>
    setProviders(providers.map((p, i) => (i === index ? { ...p, ...patch } : p)))

  const addProvider = (presetKey: string) => {
    const preset = presets[presetKey]
    const id = `${presetKey}-${providers.length + 1}`
    setProviders([...providers, {
      id, name: preset?.name || 'Sign-in', preset: presetKey, enabled: false, issuer: '', client_id: '',
      client_secret: '', client_secret_set: false, scopes: 'openid profile email',
      username_claim: preset?.username_claim || 'preferred_username', groups_claim: preset?.groups_claim ?? 'groups',
      role_map: {}, default_role: '', allowed_emails: '',
    }])
    setOpenProvider(providers.length)
  }

  const save = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const body = {
        public_base_url: publicUrl,
        allow_local_login: allowLocal,
        break_glass_usernames: breakGlass.split(',').map(s => s.trim()).filter(Boolean),
        providers: providers.map(p => ({
          id: p.id, name: p.name, preset: p.preset, enabled: p.enabled, issuer: p.issuer, client_id: p.client_id,
          client_secret: p.client_secret || null, scopes: p.scopes, username_claim: p.username_claim,
          groups_claim: p.groups_claim, role_map: p.role_map, default_role: p.default_role || null,
          allowed_emails: p.allowed_emails.split(',').map(s => s.trim()).filter(Boolean),
        })),
        ldap: ldap && { ...ldap, bind_password: ldap.bind_password || null, default_role: ldap.default_role || null },
      }
      const res = await fetch('/api/v1/admin/sso', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      const data = await res.json()
      if (!res.ok) { setMessage({ ok: false, text: typeof data.detail === 'string' ? data.detail : 'Could not save' }); return }
      setMessage({ ok: true, text: 'Saved. Sign-in options update immediately.' })
      await load()
    } finally {
      setBusy(false)
    }
  }

  const removeProvider = (i: number) => {
    if (!window.confirm(`Remove ${providers[i].name}? Users who signed in with it keep their accounts, but cannot sign in with it again.`)) return
    setProviders(providers.filter((_, j) => j !== i))
    setOpenProvider(null)
  }

  const testProvider = async (p: SsoProviderForm) => {
    setMessage(null)
    const res = await fetch(`/api/v1/admin/sso/test-oidc/${encodeURIComponent(p.id)}`, { method: 'POST' })
    const data = await res.json()
    if (!res.ok) setMessage({ ok: false, text: `${p.name}: ${data.detail}` })
    else setMessage({ ok: true, text: `${p.name}: found ${data.issuer} with ${data.signing_keys} signing key(s).` })
  }

  const testLdap = async () => {
    setLdapTestResult(null)
    const res = await fetch('/api/v1/admin/sso/test-ldap', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: ldapTestUser }),
    })
    const data = await res.json()
    if (!res.ok) setLdapTestResult(data.detail || 'Test failed')
    else if (!data.found) setLdapTestResult('No single matching entry for that user name.')
    else setLdapTestResult(`Found. Groups: ${data.groups.length ? data.groups.length : 'none'}. Role on sign-in: ${data.role || 'none (sign-in refused)'}.`)
  }

  if (!loaded || !ldap) return <p className="settings-help">Loading single sign-on settings…</p>

  const enabledCount = providers.filter(p => p.enabled).length
  const tabs: { key: 'general' | 'oidc' | 'ldap'; label: string }[] = [
    { key: 'general', label: 'Access' },
    { key: 'oidc', label: `OpenID Connect (${providers.length})` },
    { key: 'ldap', label: ldap.enabled ? 'Directory (on)' : 'Directory (off)' },
  ]

  return (
    <div>
      <div className="sso-header">
        <p>{enabledCount} sign-in option(s) shown{ldap.enabled ? ', directory sign-in on' : ''}. Local sign-in is {allowLocal ? 'on' : 'off'}.</p>
      </div>

      <div className="sso-tabs" role="tablist">
        {tabs.map(t => (
          <button key={t.key} role="tab" aria-selected={tab === t.key}
            className={`sso-tab ${tab === t.key ? 'active' : ''}`} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>

      {tab === 'general' && (
        <div className="sso-section">
          <h4>Public address</h4>
          <p>Providers send users back to this address, so it must be the address people type in their browser.</p>
          <div className="sso-grid">
            <div className="wide">
              <label>Public address of JÁRN</label>
              <input type="text" value={publicUrl} onChange={e => setPublicUrl(e.target.value)} placeholder="https://jarn.example.com" />
              {redirectUri && <div className="sso-hint">Register this redirect address with each provider: <code>{redirectUri}</code></div>}
            </div>
          </div>
        </div>
      )}

      {tab === 'general' && (
        <div className="sso-section">
          <h4>Local sign-in</h4>
          <p>Accounts that have a JÁRN password. Keep at least one such administrator, reachable when your provider is down.</p>
          <div className="sso-toggles">
            <label className="settings-toggle">
              <input type="checkbox" checked={allowLocal} onChange={e => setAllowLocal(e.target.checked)} />
              Allow password sign-in for local accounts
            </label>
          </div>
          {!allowLocal && (
            <div className="sso-grid">
              <div className="wide">
                <label>Break-glass accounts that can still use a password</label>
                <input type="text" value={breakGlass} onChange={e => setBreakGlass(e.target.value)} placeholder="administrator" />
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'oidc' && (
        <>
          {providers.length === 0 && (
            <div className="sso-section">
              <h4>No providers yet</h4>
              <p>Choose the type of provider you use. Each one fills in the usual settings for you.</p>
            </div>
          )}
          {providers.map((p, i) => {
            const open = openProvider === i
            return (
              <div className="sso-card" key={i}>
                <div className="sso-card-head">
                  <span className="name">{p.name || p.id}</span>
                  <span className={`sso-pill ${p.enabled ? 'on' : 'off'}`}>{p.enabled ? 'Shown on sign-in' : 'Hidden'}</span>
                  <span className="issuer">{p.issuer || 'No issuer set'}</span>
                  <span className="spacer" />
                  <button className="btn-edit" onClick={() => setOpenProvider(open ? null : i)}>{open ? 'Close' : 'Edit'}</button>
                </div>
                {open && (
                  <div className="sso-card-body">
                    {presets[p.preset] && <div className="sso-note">{presets[p.preset].help}</div>}
                    <div className="sso-toggles">
                      <label className="settings-toggle">
                        <input type="checkbox" checked={p.enabled} onChange={e => updateProvider(i, { enabled: e.target.checked })} />
                        Show this option on the sign-in page
                      </label>
                    </div>
                    <div className="sso-grid">
                      <div><label>Display name</label><input type="text" value={p.name} onChange={e => updateProvider(i, { name: e.target.value })} /></div>
                      <div><label>Id (used in the address)</label><input type="text" value={p.id} onChange={e => updateProvider(i, { id: e.target.value })} /></div>
                      <div className="wide"><label>Issuer</label><input type="text" value={p.issuer} onChange={e => updateProvider(i, { issuer: e.target.value })} placeholder={presets[p.preset]?.issuer_hint} /></div>
                      <div><label>Client id</label><input type="text" value={p.client_id} onChange={e => updateProvider(i, { client_id: e.target.value })} /></div>
                      <div>
                        <label>Client secret</label>
                        <input type="password" value={p.client_secret} onChange={e => updateProvider(i, { client_secret: e.target.value })}
                          placeholder={p.client_secret_set ? 'Saved, leave blank to keep' : 'Enter the secret'} />
                      </div>
                      <div><label>Username claim</label><input type="text" value={p.username_claim} onChange={e => updateProvider(i, { username_claim: e.target.value })} /></div>
                      <div>
                        <label>Groups claim</label>
                        <input type="text" value={p.groups_claim} onChange={e => updateProvider(i, { groups_claim: e.target.value })} />
                        <div className="sso-hint">Leave empty if the provider sends no groups.</div>
                      </div>
                      <div className="wide">
                        <label>Group to role</label>
                        <RoleMapEditor value={p.role_map} onChange={role_map => updateProvider(i, { role_map })} placeholder="Group name or id from the provider" />
                      </div>
                      <div>
                        <label>Role for anyone who matches no group</label>
                        <select value={p.default_role} onChange={e => updateProvider(i, { default_role: e.target.value })}>
                          {ROLE_OPTIONS.map(r => <option key={r} value={r}>{r || 'None: refuse sign-in'}</option>)}
                        </select>
                      </div>
                      <div>
                        <label>Allowed email addresses</label>
                        <input type="text" value={p.allowed_emails} onChange={e => updateProvider(i, { allowed_emails: e.target.value })} placeholder="Comma separated, optional" />
                      </div>
                    </div>
                    <div className="sso-actions">
                      <button className="btn-test" onClick={() => testProvider(p)} disabled={busy || !p.issuer}>Test provider</button>
                      <button className="btn-cancel" onClick={() => removeProvider(i)}>Remove</button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
          <div className="sso-section">
            <h4>Add a provider</h4>
            <div className="sso-tiles">
              {Object.entries(presets).filter(([k]) => k !== 'ldap').map(([k, v]) => (
                <button key={k} className="sso-tile" onClick={() => addProvider(k)}>
                  {v.name}
                  <small>{k === 'google' ? 'Access by email list' : 'Groups to roles'}</small>
                </button>
              ))}
            </div>
          </div>
        </>
      )}

      {tab === 'ldap' && (
        <>
          <div className="sso-section">
            <h4>Active Directory and LDAP</h4>
            <p>Users type their directory password into JÁRN's sign-in form. JÁRN checks it with the directory and does not store it.</p>
            <div className="sso-toggles">
              <label className="settings-toggle">
                <input type="checkbox" checked={ldap.enabled} onChange={e => setLdap({ ...ldap, enabled: e.target.checked })} />
                Use the directory for sign-in
              </label>
            </div>
          </div>

          <div className="sso-section">
            <h4>Connection</h4>
            <div className="sso-grid">
              <div><label>Server</label><input type="text" value={ldap.host} onChange={e => setLdap({ ...ldap, host: e.target.value })} placeholder="dc1.example.com" /></div>
              <div><label>Port</label><input type="number" value={ldap.port} onChange={e => setLdap({ ...ldap, port: Number(e.target.value) })} /></div>
              <div className="wide sso-toggles">
                <label className="settings-toggle"><input type="checkbox" checked={ldap.use_ssl} onChange={e => setLdap({ ...ldap, use_ssl: e.target.checked, starttls: e.target.checked ? false : ldap.starttls, port: e.target.checked ? 636 : 389 })} /> LDAPS (port 636)</label>
                <label className="settings-toggle"><input type="checkbox" checked={ldap.starttls} disabled={ldap.use_ssl} onChange={e => setLdap({ ...ldap, starttls: e.target.checked })} /> STARTTLS (port 389)</label>
                <label className="settings-toggle"><input type="checkbox" checked={ldap.verify_tls} onChange={e => setLdap({ ...ldap, verify_tls: e.target.checked })} /> Verify the server certificate</label>
              </div>
            </div>
          </div>

          <div className="sso-section">
            <h4>Service account</h4>
            <p>A read-only account JÁRN uses to search the directory. It never signs anyone in.</p>
            <div className="sso-grid">
              <div><label>Bind DN</label><input type="text" value={ldap.bind_dn} onChange={e => setLdap({ ...ldap, bind_dn: e.target.value })} placeholder="CN=jarn-svc,OU=Service,DC=example,DC=com" /></div>
              <div><label>Password</label><input type="password" value={ldap.bind_password} onChange={e => setLdap({ ...ldap, bind_password: e.target.value })} placeholder={ldap.bind_password_set ? 'Saved, leave blank to keep' : ''} /></div>
            </div>
          </div>

          <div className="sso-section">
            <h4>Users and groups</h4>
            <div className="sso-grid">
              <div className="wide"><label>Search base DN</label><input type="text" value={ldap.base_dn} onChange={e => setLdap({ ...ldap, base_dn: e.target.value })} placeholder="OU=Staff,DC=example,DC=com" /></div>
              <div className="wide"><label>User search filter</label><input type="text" value={ldap.user_filter} onChange={e => setLdap({ ...ldap, user_filter: e.target.value })} /><div className="sso-hint">{'{username}'} is replaced with the user name typed at sign-in.</div></div>
              <div><label>Username attribute</label><input type="text" value={ldap.username_attribute} onChange={e => setLdap({ ...ldap, username_attribute: e.target.value })} /><div className="sso-hint">sAMAccountName for AD, uid for OpenLDAP</div></div>
              <div><label>Email attribute</label><input type="text" value={ldap.email_attribute} onChange={e => setLdap({ ...ldap, email_attribute: e.target.value })} /></div>
              <div><label>Group attribute on the user</label><input type="text" value={ldap.group_attribute} onChange={e => setLdap({ ...ldap, group_attribute: e.target.value })} /><div className="sso-hint">memberOf in Active Directory</div></div>
              <div><label>Group search base</label><input type="text" value={ldap.group_base_dn} onChange={e => setLdap({ ...ldap, group_base_dn: e.target.value })} /><div className="sso-hint">Used when the user has no group attribute</div></div>
              <div className="wide">
                <label>Group to role</label>
                <RoleMapEditor value={ldap.role_map} onChange={role_map => setLdap({ ...ldap, role_map })} placeholder="CN=JARN-Admins,OU=Groups,DC=example,DC=com" />
              </div>
              <div>
                <label>Role for anyone who matches no group</label>
                <select value={ldap.default_role} onChange={e => setLdap({ ...ldap, default_role: e.target.value })}>
                  {ROLE_OPTIONS.map(r => <option key={r} value={r}>{r || 'None: refuse sign-in'}</option>)}
                </select>
              </div>
            </div>
          </div>

          <div className="sso-section">
            <h4>Test a user</h4>
            <p>Looks up a user name in the directory and shows the role that sign-in would grant. No password is checked.</p>
            <div className="sso-actions" style={{ marginTop: 0 }}>
              <input type="text" value={ldapTestUser} onChange={e => setLdapTestUser(e.target.value)} placeholder="User name" />
              <button className="btn-test" onClick={testLdap} disabled={busy || !ldapTestUser || !ldap.host}>Look up</button>
            </div>
            {ldapTestResult && <p className="sso-hint" style={{ marginTop: 8 }}>{ldapTestResult}</p>}
          </div>
        </>
      )}

      <div className="sso-savebar">
        <button className="btn-save-settings" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save single sign-on'}</button>
        {message && <span className="sso-message" style={{ color: message.ok ? 'var(--success)' : 'var(--error)' }}>{message.text}</span>}
      </div>
    </div>
  )
}

interface HostKeyRow {
  server_id: string
  hostname: string
  address: string
  status?: 'approved' | 'unapproved' | 'changed' | 'unreachable'
  detail?: string
  pinned_fingerprint: string | null
  host_key_fingerprint?: string | null
}

const HOST_KEY_STATUS: Record<string, { label: string; color: string }> = {
  approved: { label: 'Approved', color: 'var(--success)' },
  unapproved: { label: 'Not approved', color: 'var(--warning)' },
  changed: { label: 'Key changed', color: 'var(--error)' },
  unreachable: { label: 'Unreachable', color: 'var(--text-secondary)' },
}

function HostKeysSection() {
  const [rows, setRows] = useState<HostKeyRow[]>([])
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const load = async () => {
    try {
      const res = await fetch('/api/v1/admin/host-keys')
      if (!res.ok) throw new Error()
      setRows(await res.json())
      setLoaded(true)
    } catch {
      setMessage({ ok: false, text: 'Could not load server host keys' })
    }
  }

  useEffect(() => { load() }, [])

  const fetchAll = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const res = await fetch('/api/v1/admin/host-keys/fetch', { method: 'POST' })
      if (!res.ok) throw new Error()
      setRows(await res.json())
      setLoaded(true)
      setMessage({ ok: true, text: 'Read the current key of every server. Nothing was sent to them but the connection itself.' })
    } catch {
      setMessage({ ok: false, text: 'Could not read the host keys' })
    } finally {
      setBusy(false)
    }
  }

  const approve = async (row: HostKeyRow) => {
    if (!row.host_key_fingerprint) return
    const question = row.status === 'changed'
      ? `${row.hostname} now presents a different key.\n\nOnly approve if the server's owner confirmed the change.\n\nFingerprint: ${row.host_key_fingerprint}`
      : `Approve this key for ${row.hostname}?\n\nFirst compare the fingerprint with the one printed on the server (ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub).\n\nFingerprint: ${row.host_key_fingerprint}`
    if (!window.confirm(question)) return
    setBusy(true)
    try {
      const res = await fetch(`/api/v1/admin/host-keys/${row.server_id}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fingerprint: row.host_key_fingerprint }),
      })
      const data = await res.json()
      if (!res.ok) {
        setMessage({ ok: false, text: data.detail || 'Approval failed' })
      } else {
        setMessage({ ok: true, text: `Approved for ${row.hostname}` })
      }
      await fetchAll()
    } finally {
      setBusy(false)
    }
  }

  const clear = async (row: HostKeyRow) => {
    if (!window.confirm(`Remove the approved key for ${row.hostname}? It cannot be patched until a key is approved again.`)) return
    setBusy(true)
    try {
      await fetch(`/api/v1/admin/host-keys/${row.server_id}`, { method: 'DELETE' })
      await fetchAll()
    } finally {
      setBusy(false)
    }
  }

  const unapproved = rows.filter(r => r.status && r.status !== 'approved').length

  return (
    <div className="settings-card">
      <h3>Servers</h3>
      <p className="settings-help">
        {loaded
          ? `${rows.length} server(s). ${rows.length - unapproved} approved, ${unapproved} need attention.`
          : 'Loading…'}
      </p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <button className="btn-save-settings" onClick={fetchAll} disabled={busy}>Read keys from all servers</button>
      </div>
      <div className="risk-table-wrap">
        <table className="risk-table">
          <thead>
            <tr><th>Server</th><th>Address</th><th>Approved fingerprint</th><th>Server presents</th><th>Status</th><th></th></tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.server_id}>
                <td>{row.hostname}</td>
                <td>{row.address}</td>
                <td><code>{row.pinned_fingerprint ?? 'none'}</code></td>
                <td><code>{row.host_key_fingerprint ?? '–'}</code></td>
                <td>
                  {row.status
                    ? <span style={{ color: HOST_KEY_STATUS[row.status]?.color }}>{HOST_KEY_STATUS[row.status]?.label}</span>
                    : <span className="settings-help">Not read yet</span>}
                  {row.detail && row.status !== 'approved' && <div className="settings-help">{row.detail}</div>}
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {row.host_key_fingerprint && row.status !== 'approved' && (
                    <button className="btn-edit" onClick={() => approve(row)} disabled={busy}>
                      {row.status === 'changed' ? 'Approve new key' : 'Approve'}
                    </button>
                  )}
                  {row.pinned_fingerprint && (
                    <button className="btn-cancel" onClick={() => clear(row)} disabled={busy} style={{ marginLeft: 6 }}>Remove</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {message && (
        <p className="settings-help" style={{ color: message.ok ? 'var(--success)' : 'var(--error)' }}>{message.text}</p>
      )}
    </div>
  )
}

interface OffsiteForm {
  enabled: boolean
  host: string
  port: number
  username: string
  auth: 'key' | 'password'
  private_key: string
  password: string
  remote_dir: string
  keep_copies: number
  host_key: string | null
}

interface OffsiteView extends Omit<OffsiteForm, 'private_key' | 'password'> {
  state: string
  waiver: { reason: string; by: string; at: string } | null
  host_key_fingerprint: string | null
  private_key_set: boolean
  password_set: boolean
  last_run: { at: string; ok: boolean; summary: string } | null
}

const EMPTY_OFFSITE: OffsiteForm = {
  enabled: false, host: '', port: 22, username: '', auth: 'key', private_key: '', password: '',
  remote_dir: '', keep_copies: 14, host_key: null,
}

function OffsiteSection() {
  const [form, setForm] = useState<OffsiteForm>(EMPTY_OFFSITE)
  const [view, setView] = useState<OffsiteView | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [tested, setTested] = useState<{ fingerprint: string; line: string; pinned: boolean } | null>(null)
  const [waiverReason, setWaiverReason] = useState('')

  const applyView = (data: OffsiteView) => {
    setView(data)
    setForm({ ...EMPTY_OFFSITE, ...data, private_key: '', password: '' })
  }

  const load = async () => {
    try {
      const res = await fetch('/api/v1/admin/offsite')
      if (!res.ok) throw new Error()
      applyView(await res.json())
    } catch {
      setMessage({ ok: false, text: 'Could not load off-host copy settings' })
    }
  }

  useEffect(() => { load() }, [])

  const set = <K extends keyof OffsiteForm>(key: K, value: OffsiteForm[K]) => {
    setForm({ ...form, [key]: value })
    if (key !== 'enabled') setTested(null)
  }

  const payload = () => JSON.stringify({ ...form, host_key: form.host_key })

  const test = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const res = await fetch('/api/v1/admin/offsite/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: payload() })
      const data = await res.json()
      if (!res.ok) { setMessage({ ok: false, text: data.detail || 'Connection test failed' }); return }
      setTested({ fingerprint: data.host_key_fingerprint, line: data.host_key, pinned: data.host_key_pinned })
      setMessage({
        ok: true,
        text: `Connected. Found ${data.remote_dump_count} database dump(s) and ${data.remote_audit_count} audit archive(s) in the remote folder.`,
      })
    } catch {
      setMessage({ ok: false, text: 'Connection test failed' })
    } finally {
      setBusy(false)
    }
  }

  const approveKey = () => {
    if (!tested) return
    setForm({ ...form, host_key: tested.line })
    setTested({ ...tested, pinned: true })
  }

  const save = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const res = await fetch('/api/v1/admin/offsite', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: payload() })
      const data = await res.json()
      if (!res.ok) { setMessage({ ok: false, text: data.detail || 'Could not save' }); return }
      applyView(data)
      setTested(null)
      setMessage({ ok: true, text: form.enabled ? 'Off-host copy saved. It runs daily at 03:30 UTC.' : 'Off-host copy saved and turned off' })
    } catch {
      setMessage({ ok: false, text: 'Could not save' })
    } finally {
      setBusy(false)
    }
  }

  const runNow = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const res = await fetch('/api/v1/admin/offsite/run', { method: 'POST' })
      const data = await res.json()
      if (!res.ok) { setMessage({ ok: false, text: data.detail || 'Could not start the copy' }); return }
      setMessage({ ok: true, text: 'Copy running in the background. The last run below updates when it finishes.' })
      for (let i = 0; i < 120; i++) {
        await new Promise(resolve => setTimeout(resolve, 5000))
        const status = await (await fetch(`/api/v1/backup/tasks/${data.task_id}`)).json()
        if (status.state === 'SUCCESS' || status.state === 'FAILURE') break
      }
      await load()
    } catch {
      setMessage({ ok: false, text: 'Could not start the copy' })
    } finally {
      setBusy(false)
    }
  }

  if (!view) return <p className="settings-help">Loading off-host copy settings…</p>

  return (
    <div>
      <div className="settings-card">
        <h3>Destination</h3>
        <label className="settings-toggle">
          <input type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} />
          Copy backups off this host every day
        </label>
        <div className="settings-field">
          <label>Server (host name or IP)</label>
          <input type="text" value={form.host} onChange={(e) => set('host', e.target.value)} placeholder="nas.example.com" />
        </div>
        <div className="settings-row">
          <div className="settings-field">
            <label>SSH port</label>
            <input type="number" value={form.port} onChange={(e) => set('port', Number(e.target.value))} />
          </div>
          <div className="settings-field">
            <label>Username</label>
            <input type="text" value={form.username} onChange={(e) => set('username', e.target.value)} />
          </div>
        </div>
        <div className="settings-field">
          <label>Remote folder</label>
          <input type="text" value={form.remote_dir} onChange={(e) => set('remote_dir', e.target.value)} placeholder="/volume1/jarn-backups" />
        </div>
        <div className="settings-field">
          <label>Copies to keep on the remote server (per kind)</label>
          <input type="number" min={1} max={365} value={form.keep_copies} onChange={(e) => set('keep_copies', Number(e.target.value))} />
        </div>
      </div>

      <div className="settings-card">
        <h3>Sign-in</h3>
        <div className="settings-field">
          <label>Method</label>
          <select value={form.auth} onChange={(e) => set('auth', e.target.value as 'key' | 'password')} className="form-input">
            <option value="key">SSH private key</option>
            <option value="password">Password</option>
          </select>
        </div>
        {form.auth === 'key' ? (
          <div className="settings-field">
            <label>Private key (OpenSSH or PEM, no passphrase)</label>
            <textarea rows={4} value={form.private_key} onChange={(e) => set('private_key', e.target.value)}
              placeholder={view.private_key_set ? 'Saved (leave blank to keep)' : 'Paste the private key'} />
          </div>
        ) : (
          <div className="settings-field">
            <label>Password</label>
            <input type="password" value={form.password} onChange={(e) => set('password', e.target.value)}
              placeholder={view.password_set ? 'Saved (leave blank to keep)' : 'Enter the password'} />
          </div>
        )}
        <p className="settings-help">
          Keys and passwords are stored encrypted and are not shown again. Use an account that can only write to the
          remote folder.
        </p>
      </div>

      <div className="settings-card">
        <h3>Server identity</h3>
        <p className="settings-help">
          Test the connection first. It shows the server's SSH host key fingerprint. Compare it with the fingerprint
          printed by the NAS or server itself, then approve it. Copies are only sent to a server whose key was approved.
        </p>
        <p className="settings-help">
          Approved key: {form.host_key ? (view.host_key_fingerprint ?? 'set') : 'none yet'}
        </p>
        {tested && (
          <p className="settings-help">
            Server presents: <code>{tested.fingerprint}</code> {tested.pinned ? '(approved)' : '(not approved yet)'}
          </p>
        )}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn-test" onClick={test} disabled={busy || !form.host || !form.username || !form.remote_dir}>Test connection</button>
          <button className="btn-edit" onClick={approveKey} disabled={busy || !tested || tested.pinned}>Approve this server key</button>
        </div>
      </div>

      <div className="settings-card">
        <h3>Waiver</h3>
        <p className="settings-help">
          Without an off-host copy, backups exist only on this server. You can accept that risk here, with a reason.
          The dashboard keeps showing the warning, and the alert stays open, until a copy is turned on.
        </p>
        {view.waiver ? (
          <>
            <p className="settings-help">
              Accepted by {view.waiver.by} on {new Date(view.waiver.at).toLocaleDateString()}: {view.waiver.reason}
            </p>
            <button className="btn-cancel" disabled={busy} onClick={async () => {
              setBusy(true)
              try {
                await fetch('/api/v1/admin/offsite/waiver', { method: 'DELETE' })
                await load()
              } finally {
                setBusy(false)
              }
            }}>Remove waiver</button>
          </>
        ) : (
          <>
            <div className="settings-field">
              <label>Reason (required)</label>
              <input type="text" value={waiverReason} onChange={(e) => setWaiverReason(e.target.value)}
                placeholder="e.g. Test lab; backups are rebuilt from source" />
            </div>
            <button className="btn-save-settings" disabled={busy || waiverReason.trim().length < 5} onClick={async () => {
              setBusy(true)
              setMessage(null)
              try {
                const res = await fetch('/api/v1/admin/offsite/waiver', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ reason: waiverReason.trim() }),
                })
                const data = await res.json()
                if (!res.ok) setMessage({ ok: false, text: data.detail || 'Could not record the waiver' })
                else { setWaiverReason(''); applyView(data) }
              } finally {
                setBusy(false)
              }
            }}>Record waiver</button>
          </>
        )}
      </div>

      <div className="settings-card">
        <h3>Status</h3>
        <p className="settings-help">
          {view.last_run
            ? `Last run ${new Date(view.last_run.at).toLocaleString()}: ${view.last_run.ok ? 'succeeded' : 'failed'} (${view.last_run.summary})`
            : 'No run yet'}
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn-save-settings" onClick={save} disabled={busy || (form.enabled && !form.host_key)}>Save</button>
          <button className="btn-edit" onClick={runNow} disabled={busy || !view.enabled}>Copy now</button>
        </div>
        {form.enabled && !form.host_key && (
          <p className="settings-help">Test the connection and approve the server key before saving with the copy turned on.</p>
        )}
      </div>

      {message && (
        <p className="settings-help" style={{ color: message.ok ? 'var(--success)' : 'var(--error)' }}>{message.text}</p>
      )}
    </div>
  )
}

interface UserRow { id: string; username: string; email: string | null; roles: string[]; is_active: boolean; mfa_enabled: boolean }

function UsersSection() {
  const [users, setUsers] = useState<UserRow[]>([])
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', email: '', role: 'viewer' })
  const [resetPassword, setResetPassword] = useState<Record<string, string>>({})

  const load = async () => {
    const res = await fetch('/api/v1/users')
    if (res.ok) setUsers((await res.json()).users)
  }

  useEffect(() => { load() }, [])

  const call = async (method: string, path: string, body?: object) => {
    setBusy(true)
    setMessage(null)
    try {
      const res = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setMessage({ ok: false, text: typeof data.detail === 'string' ? data.detail : 'Request failed' })
        return false
      }
      await load()
      return true
    } finally {
      setBusy(false)
    }
  }

  const deleteUser = async (u: UserRow) => {
    const question = `Delete ${u.username}?\n\nThey lose all access, and their password and MFA are removed. Their past actions stay in the audit trail.`
    if (!window.confirm(question)) return
    if (await call('DELETE', `/api/v1/users/${u.id}`)) setMessage({ ok: true, text: `${u.username} deleted` })
  }

  const addUser = async () => {
    const ok = await call('POST', '/api/v1/users', { username: form.username, password: form.password, email: form.email || null, role: form.role })
    if (ok) {
      setForm({ username: '', password: '', email: '', role: 'viewer' })
      setMessage({ ok: true, text: 'User added' })
    }
  }

  return (
    <div>
      <div className="settings-card">
        <h3>Users</h3>
        <p className="settings-help">
          Viewers can read everything. Operators can also run jobs, change servers and schedules. Admins can also change
          settings, users, certificates and notifications. At least one active admin is always kept.
        </p>
        <table className="servers-table">
          <thead>
            <tr><th>Username</th><th>Role</th><th>Active</th><th>MFA</th><th>Reset password</th><th></th></tr>
          </thead>
          <tbody>
            {users.map(u => (
              <tr key={u.id}>
                <td>{u.username}{u.email ? <div className="settings-help">{u.email}</div> : null}</td>
                <td>
                  <select value={u.roles[0] ?? 'viewer'} disabled={busy} onChange={e => call('PUT', `/api/v1/users/${u.id}`, { role: e.target.value })}>
                    <option value="viewer">viewer</option>
                    <option value="operator">operator</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
                <td>
                  <input type="checkbox" checked={u.is_active} disabled={busy} onChange={e => call('PUT', `/api/v1/users/${u.id}`, { is_active: e.target.checked })} />
                </td>
                <td>{u.mfa_enabled ? 'On' : 'Off'}</td>
                <td>
                  <input type="password" placeholder="New password" value={resetPassword[u.id] ?? ''}
                    onChange={e => setResetPassword({ ...resetPassword, [u.id]: e.target.value })} />
                  <button className="btn-edit" disabled={busy || !resetPassword[u.id]}
                    onClick={async () => { if (await call('PUT', `/api/v1/users/${u.id}`, { password: resetPassword[u.id] })) setResetPassword({ ...resetPassword, [u.id]: '' }) }}>
                    Set
                  </button>
                </td>
                <td>
                  {getUser()?.username !== u.username && (
                    <button className="btn-cancel" disabled={busy} onClick={() => deleteUser(u)}>Delete</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="settings-card">
        <h3>Add user</h3>
        <div className="settings-field"><label>Username</label><input value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></div>
        <div className="settings-field"><label>Password</label><input type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></div>
        <div className="settings-field"><label>Email (optional)</label><input value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></div>
        <div className="settings-field">
          <label>Role</label>
          <select value={form.role} onChange={e => setForm({ ...form, role: e.target.value })}>
            <option value="viewer">viewer (read only)</option>
            <option value="operator">operator</option>
            <option value="admin">admin</option>
          </select>
        </div>
        <button className="btn-save-settings" onClick={addUser} disabled={busy || !form.username || !form.password}>Add user</button>
      </div>
      {message && <p className="settings-help" style={{ color: message.ok ? 'var(--success)' : 'var(--error)' }}>{message.text}</p>}
    </div>
  )
}

function KubernetesSection({ settings, update }: { settings: Settings; update: Update }) {
  const [servers, setServers] = useState<{ id: string; hostname: string }[]>([])

  useEffect(() => {
    fetch(`/api/v1/servers`)
      .then(res => (res.ok ? res.json() : []))
      .then(data => setServers(data))
      .catch(() => setServers([]))
  }, [])

  const current = settings.general.kubernetes_control_server_id || ''

  return (
    <div className="settings-field">
      <label htmlFor="k8s-control">Control host (server with /etc/kubernetes/admin.conf)</label>
      <select
        id="k8s-control"
        value={current}
        onChange={(e) => update({ general: { ...settings.general, kubernetes_control_server_id: e.target.value || null } })}
      >
        <option value="">Not set: drain on each node (works only on control-plane nodes)</option>
        {servers.map(srv => (
          <option key={srv.id} value={srv.id}>{srv.hostname}</option>
        ))}
      </select>
      <p className="settings-help">
        Choose any control-plane node. It must have the admin kubeconfig and kubectl. Save settings to apply.
      </p>
    </div>
  )
}

function BackupSection({ settings, update }: { settings: Settings; update: Update }) {
  const b = settings.backup
  const [testResults, setTestResults] = useState<any[] | null>(null)
  const [testing, setTesting] = useState(false)
  const [testError, setTestError] = useState<string | null>(null)

  const setTargets = (targets: BackupTarget[]) => update({ backup: { ...b, targets } })
  const patchTarget = (id: string, patch: Partial<BackupTarget>) =>
    setTargets(b.targets.map(t => (t.id === id ? { ...t, ...patch } : t)))
  const addTarget = () =>
    setTargets([
      ...b.targets,
      {
        id: `pve-${Math.random().toString(36).slice(2, 8)}`,
        name: `Proxmox ${b.targets.length + 1}`,
        provider: 'proxmox',
        api_url: '',
        token_id: '',
        storage: '',
        verify_tls: false,
        token_secret_set: false,
        token_secret: '',
      },
    ])
  const removeTarget = (id: string) => setTargets(b.targets.filter(t => t.id !== id))

  const test = async () => {
    setTesting(true)
    setTestResults(null)
    setTestError(null)
    try {
      const res = await fetch(`/api/v1/backup/test`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) setTestError(data.detail || 'Connection test failed')
      else setTestResults(data.results)
    } catch (err) {
      setTestError('Could not reach the backend')
    } finally {
      setTesting(false)
    }
  }

  return (
    <>
      <div className="settings-field">
        <label className="settings-toggle">
          <input type="checkbox" checked={b.enabled} onChange={(e) => update({ backup: { ...b, enabled: e.target.checked } })} />
          <span>Enable backups</span>
        </label>
      </div>

      {b.targets.length === 0 && (
        <p className="settings-help">No Proxmox VE servers added yet. Add one for each Proxmox host that runs your VMs.</p>
      )}

      {b.targets.map(t => (
        <div key={t.id} className="settings-card">
          <div className="settings-row">
            <div className="settings-field">
              <label>Name</label>
              <input type="text" value={t.name} onChange={(e) => patchTarget(t.id, { name: e.target.value })} />
            </div>
            <div className="settings-field">
              <label>Fallback storage (optional)</label>
              <input type="text" placeholder="Leave empty to use each VM's Datacenter backup job" value={t.storage || ''} onChange={(e) => patchTarget(t.id, { storage: e.target.value })} />
            </div>
          </div>
          <div className="settings-field">
            <label>API URL</label>
            <input type="text" placeholder="https://pve1.example.com:8006" value={t.api_url || ''} onChange={(e) => patchTarget(t.id, { api_url: e.target.value })} />
          </div>
          <div className="settings-row">
            <div className="settings-field">
              <label>API token ID</label>
              <input type="text" placeholder="backup@pve!patchmanager" value={t.token_id || ''} onChange={(e) => patchTarget(t.id, { token_id: e.target.value })} />
            </div>
            <div className="settings-field">
              <label>API token secret</label>
              <input
                type="password"
                placeholder={t.token_secret_set ? 'Stored; leave blank to keep' : 'Paste token secret'}
                value={t.token_secret || ''}
                onChange={(e) => patchTarget(t.id, { token_secret: e.target.value })}
              />
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-field">
              <label className="settings-toggle">
                <input type="checkbox" checked={t.verify_tls} onChange={(e) => patchTarget(t.id, { verify_tls: e.target.checked })} />
                <span>Verify TLS certificate</span>
              </label>
            </div>
            <div className="settings-field">
              <button onClick={() => removeTarget(t.id)}>Remove this Proxmox server</button>
            </div>
          </div>
        </div>
      ))}

      <div className="settings-field">
        <button onClick={addTarget}>+ Add Proxmox server</button>
        <button className="btn-save-settings" onClick={test} disabled={testing || b.targets.length === 0} style={{ marginLeft: 8 }}>
          {testing ? 'Testing…' : 'Test all connections'}
        </button>
        {testError && <p className="settings-help" style={{ color: '#c8514c' }}>{testError}</p>}
        {testResults && testResults.map((r: any) => (
          <p key={r.target} className="settings-help" style={{ color: r.ok ? '#3a9d6e' : '#c8514c' }}>
            {r.name}: {r.ok ? (r.storage ? `connected to Proxmox VE ${r.pve_version}; storage '${r.storage}' accepts backups` : `connected to Proxmox VE ${r.pve_version}; storage taken from each VM's backup job`) : r.message}
          </p>
        ))}
        <p className="settings-help">Save settings before testing. Tests use the saved values.</p>
      </div>

      <h3 className="settings-subhead">Server to VM mapping</h3>
      <p className="settings-help">
        Each server is backed up from the Proxmox VM or container it runs on. Pick the VM and its Proxmox server below.
      </p>
      <BackupMappings />
    </>
  )
}

export function AdminSettings() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [activeId, setActiveId] = useState(SECTIONS[0].id)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(apiUrl())
        if (!res.ok) throw new Error('Failed to load settings')
        setSettings(await res.json())
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load settings')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  const update: Update = (patch) => {
    setSettings(prev => (prev ? { ...prev, ...patch } : prev))
  }

  const save = async () => {
    if (!settings) return
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const res = await fetch(apiUrl(), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          general: settings.general,
          backup: {
            enabled: settings.backup.enabled,
            targets: settings.backup.targets.map(t => ({
              id: t.id,
              name: t.name,
              provider: t.provider,
              api_url: t.api_url,
              token_id: t.token_id,
              storage: t.storage,
              verify_tls: t.verify_tls,
              token_secret: t.token_secret || null,
            })),
          },
          patching_config: settings.patching_config,
          auto_approve_critical: settings.auto_approve_critical,
          require_approval_for_kernel: settings.require_approval_for_kernel,
          maintenance_window_start: settings.maintenance_window_start,
          maintenance_window_end: settings.maintenance_window_end,
        }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail || 'Failed to save settings')
        return
      }
      setSettings(data)
      setCachedDefaultPageSize(data.general.default_page_size)
      setDisplayTimezone(data.general.timezone)
      setSuccess('Settings saved')
      setTimeout(() => setSuccess(null), 3000)
    } catch (err) {
      setError('Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="admin-settings-state">Loading settings…</div>
  if (!settings) return <div className="admin-settings-state">{error || 'Settings unavailable'}</div>

  const active = SECTIONS.find(s => s.id === activeId) ?? SECTIONS[0]

  return (
    <div className="admin-settings">
      <div className="page-header">
        <h1>Admin Settings</h1>
        <p>System-wide configuration</p>
      </div>

      {error && (
        <div className="error-message">
          <span>{error}</span>
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}
      {success && <div className="success-message">{success}</div>}

      <div className="admin-settings-layout">
        <nav className="admin-settings-nav" aria-label="Settings sections">
          {SECTIONS.map(section => (
            <button
              key={section.id}
              className={section.id === active.id ? 'active' : ''}
              onClick={() => setActiveId(section.id)}
            >
              {section.title}
            </button>
          ))}
        </nav>

        <section className="admin-settings-panel">
          <h2>{active.title}</h2>
          <p className="settings-section-desc">{active.description}</p>
          {active.render(settings, update)}

          <div className="settings-footer">
            <button className="btn-save-settings" onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save settings'}
            </button>
          </div>
        </section>
      </div>
    </div>
  )
}
