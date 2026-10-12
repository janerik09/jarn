import { useEffect, useState, type ReactNode } from 'react'
import { getUser, onSessionChange, renewSession, setSession, type SessionUser } from '../lib/auth'
import './SignIn.css'
import { JarnBrand } from './JarnBrand'

type Phase = 'checking' | 'setup' | 'signin' | 'ready'

async function readJson(res: Response) {
  try {
    return await res.json()
  } catch {
    return {}
  }
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>('checking')
  const [user, setUser] = useState<SessionUser | null>(getUser())

  useEffect(() => onSessionChange(() => {
    setUser(getUser())
    if (!getUser()) setPhase(p => (p === 'ready' ? 'signin' : p))
  }), [])

  useEffect(() => {
    (async () => {
      const status = await readJson(await fetch('/api/v1/auth/status'))
      if (status.setup_required) {
        setPhase('setup')
        return
      }
      if (await renewSession()) {
        setUser(getUser())
        setPhase('ready')
      } else {
        setPhase('signin')
      }
    })().catch(() => setPhase('signin'))
  }, [])

  useEffect(() => {
    if (user && phase !== 'ready') setPhase('ready')
  }, [user, phase])

  if (phase === 'checking') return <div className="signin-loading">Loading…</div>
  if (phase === 'setup') return <SetupForm onDone={() => setPhase('ready')} />
  if (phase === 'signin') return <SignInForm onDone={() => setPhase('ready')} />
  return <>{children}</>
}

function SetupForm({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState('admin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (password !== confirm) {
      setError('The two passwords do not match')
      return
    }
    setBusy(true)
    try {
      const res = await fetch('/api/v1/auth/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, email: email || null }),
      })
      const data = await readJson(res)
      if (!res.ok) {
        setError(typeof data.detail === 'string' ? data.detail : 'Setup failed')
        return
      }
      setSession({ access_token: data.access_token, user: data.user })
      onDone()
    } catch {
      setError('Could not reach the server')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="signin-page">
      <header className="signin-brand"><JarnBrand large /></header>
      <form className="signin-card" onSubmit={submit}>
        <h1>Welcome to JÁRN</h1>
        <p className="signin-help">Create the first administrator account. You will use it to sign in and to manage users.</p>
        <label>Username<input value={username} onChange={e => setUsername(e.target.value)} autoFocus required /></label>
        <label>Email (optional)<input type="email" value={email} onChange={e => setEmail(e.target.value)} /></label>
        <label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
        <label>Confirm password<input type="password" value={confirm} onChange={e => setConfirm(e.target.value)} required /></label>
        {error && <p className="signin-error">{error}</p>}
        <button className="btn-save-settings" type="submit" disabled={busy}>{busy ? 'Creating…' : 'Create administrator'}</button>
      </form>
    </div>
  )
}

interface SignInOptions {
  oidc: { id: string; name: string }[]
  ldap: boolean
  local_login: boolean
}

// Reads the failure reported back by the single sign-on callback, then removes it from the address bar
function readSsoFailure(): string | null {
  const params = new URLSearchParams(window.location.search)
  if (params.get('sso') !== 'failed') return null
  const reason = params.get('reason') || 'Single sign-on did not complete. Try again.'
  window.history.replaceState({}, '', window.location.pathname)
  return reason
}

function SignInForm({ onDone }: { onDone: () => void }) {
  const [options, setOptions] = useState<SignInOptions>({ oidc: [], ldap: false, local_login: true })
  const [ssoError] = useState<string | null>(readSsoFailure)
  useEffect(() => {
    fetch('/api/v1/auth/providers').then(r => (r.ok ? r.json() : null)).then(data => data && setOptions(data)).catch(() => undefined)
  }, [])
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [needCode, setNeedCode] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const res = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, code: needCode ? code : null }),
      })
      const data = await readJson(res)
      if (!res.ok) {
        if (res.status >= 500) {
          setError('The server is restarting. Wait a moment and try again.')
        } else {
          setError(typeof data.detail === 'string' ? data.detail : 'Sign-in failed')
        }
        return
      }
      if (data.mfa_required) {
        setNeedCode(true)
        setError('Enter the 6-digit code from your authenticator app, or a recovery code.')
        return
      }
      setSession({ access_token: data.access_token, user: data.user })
      onDone()
    } catch {
      setError('Could not reach the server')
    } finally {
      setBusy(false)
    }
  }

  const passwordAllowed = options.local_login || options.ldap
  return (
    <div className="signin-page">
      <header className="signin-brand"><JarnBrand large /></header>
      <div className="signin-card">
        <h1>Sign in to JÁRN</h1>
        {ssoError && <p className="signin-error" role="alert">{ssoError}</p>}
        {options.oidc.map(p => (
          <a key={p.id} className="btn-save-settings" href={`/api/v1/auth/sso/start/${encodeURIComponent(p.id)}`}
            style={{ display: 'block', textAlign: 'center', textDecoration: 'none', marginBottom: 8 }}>
            Sign in with {p.name}
          </a>
        ))}
        {options.oidc.length > 0 && passwordAllowed && <p className="signin-help" style={{ textAlign: 'center' }}>or</p>}
        {passwordAllowed ? (
          <form onSubmit={submit} style={{ display: 'contents' }}>
            <label>Username<input value={username} onChange={e => setUsername(e.target.value)} autoFocus required disabled={needCode} /></label>
            <label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} required disabled={needCode} /></label>
            {needCode && (
              <label>Authenticator code<input inputMode="text" autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} autoFocus required /></label>
            )}
            {error && <p className={needCode && !error.startsWith('Enter') ? 'signin-error' : 'signin-help'}>{error}</p>}
            <button className="btn-save-settings" type="submit" disabled={busy}>{busy ? 'Signing in…' : needCode ? 'Verify' : 'Sign in'}</button>
          </form>
        ) : (
          <p className="signin-help">Password sign-in is turned off. Use one of the sign-in options above.</p>
        )}
      </div>
    </div>
  )
}
