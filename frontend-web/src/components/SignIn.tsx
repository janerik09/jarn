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

function SignInForm({ onDone }: { onDone: () => void }) {
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

  return (
    <div className="signin-page">
      <header className="signin-brand"><JarnBrand large /></header>
      <form className="signin-card" onSubmit={submit}>
        <h1>Sign in to JÁRN</h1>
        <label>Username<input value={username} onChange={e => setUsername(e.target.value)} autoFocus required disabled={needCode} /></label>
        <label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} required disabled={needCode} /></label>
        {needCode && (
          <label>Authenticator code<input inputMode="text" autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} autoFocus required /></label>
        )}
        {error && <p className={needCode && !error.startsWith('Enter') ? 'signin-error' : 'signin-help'}>{error}</p>}
        <button className="btn-save-settings" type="submit" disabled={busy}>{busy ? 'Signing in…' : needCode ? 'Verify' : 'Sign in'}</button>
      </form>
    </div>
  )
}
