import { useEffect, useState } from 'react'
import { getUser, renewSession } from '../lib/auth'
import './Account.css'

interface MfaSetup { secret: string; uri: string; issuer: string }

export function Account() {
  const user = getUser()
  const [enabled, setEnabled] = useState<boolean>(user?.mfa_enabled ?? false)
  const [setup, setSetup] = useState<MfaSetup | null>(null)
  const [setupCode, setSetupCode] = useState('')
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null)
  const [disablePassword, setDisablePassword] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { setEnabled(user?.mfa_enabled ?? false) }, [user?.mfa_enabled])

  const call = async (path: string, body?: object) => {
    const res = await fetch(`/api/v1/auth${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed')
    return data
  }

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setMessage(null)
    try {
      await fn()
    } catch (err) {
      setMessage({ ok: false, text: (err as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const changePassword = () => run(async () => {
    if (newPassword !== confirmPassword) throw new Error('The new passwords do not match')
    await call('/password', { current_password: currentPassword, new_password: newPassword })
    setCurrentPassword(''); setNewPassword(''); setConfirmPassword('')
    setMessage({ ok: true, text: 'Password changed' })
  })

  const startMfa = () => run(async () => {
    const data = await call('/mfa/setup')
    setSetup(data)
    setRecoveryCodes(null)
  })

  const enableMfa = () => run(async () => {
    const data = await call('/mfa/enable', { code: setupCode })
    setRecoveryCodes(data.recovery_codes)
    setSetup(null)
    setSetupCode('')
    setEnabled(true)
    await renewSession()
    setMessage({ ok: true, text: 'MFA is on. Save the recovery codes below now; they are shown only once.' })
  })

  const disableMfa = () => run(async () => {
    await call('/mfa/disable', { password: disablePassword })
    setDisablePassword('')
    setEnabled(false)
    setRecoveryCodes(null)
    setMessage({ ok: true, text: 'MFA is off' })
  })

  return (
    <div className="account-page">
      <div className="page-header">
        <h1>Account</h1>
        <p>Signed in as <strong>{user?.username}</strong> ({user?.roles.join(', ') || 'no role'})</p>
      </div>

      <div className="account-card">
        <h3>Change password</h3>
        <label>Current password<input type="password" value={currentPassword} onChange={e => setCurrentPassword(e.target.value)} /></label>
        <label>New password<input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} /></label>
        <label>Confirm new password<input type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} /></label>
        <button className="btn-save-settings" onClick={changePassword} disabled={busy || !currentPassword || !newPassword}>Change password</button>
      </div>

      <div className="account-card">
        <h3>Multi-factor authentication (MFA)</h3>
        <p className="account-help">
          Optional. When MFA is on, signing in also asks for a 6-digit code from an authenticator app. It works with
          Google Authenticator, Microsoft Authenticator, Authy, 1Password, Bitwarden and other apps that support TOTP.
        </p>
        <p className="account-help">Status: <strong>{enabled ? 'On' : 'Off'}</strong></p>

        {!enabled && !setup && (
          <button className="btn-save-settings" onClick={startMfa} disabled={busy}>Set up MFA</button>
        )}

        {setup && (
          <div className="account-step">
            <p className="account-help">
              In your authenticator app choose <em>Add account</em> and then <em>Enter a setup key</em>. Use the details
              below. Account name: <strong>{user?.username}</strong>, key type: time-based.
            </p>
            <label>Setup key<input readOnly value={setup.secret} onFocus={e => e.currentTarget.select()} /></label>
            <p className="account-help">Type of key: time-based (TOTP), 6 digits, 30 seconds, SHA-1.</p>
            <label>Enter the 6-digit code from the app to confirm<input inputMode="numeric" value={setupCode} onChange={e => setSetupCode(e.target.value)} /></label>
            <button className="btn-save-settings" onClick={enableMfa} disabled={busy || setupCode.length < 6}>Turn on MFA</button>
          </div>
        )}

        {recoveryCodes && (
          <div className="account-step">
            <p className="account-help">
              Recovery codes: each works once in place of an app code, if you lose your phone. Store them somewhere safe.
            </p>
            <pre className="recovery-codes">{recoveryCodes.join('\n')}</pre>
          </div>
        )}

        {enabled && (
          <div className="account-step">
            <label>Password to turn it off<input type="password" value={disablePassword} onChange={e => setDisablePassword(e.target.value)} /></label>
            <button className="btn-cancel" onClick={disableMfa} disabled={busy || !disablePassword}>Turn off MFA</button>
          </div>
        )}
      </div>

      {message && <p className="account-message" style={{ color: message.ok ? 'var(--success)' : 'var(--error)' }}>{message.text}</p>}
    </div>
  )
}
