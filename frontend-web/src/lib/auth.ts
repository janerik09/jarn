// Session handling for the browser. The access token lives in memory and expires after 15 minutes. The refresh
// token is an HttpOnly cookie set by the server, so page scripts cannot read it and a reload keeps the session
// through a silent renewal. Every /api/v1 request gets the access token added here, and an expired access token
// is renewed once before the request is retried.

export interface SessionUser {
  id: string
  username: string
  email: string | null
  roles: string[]
  level: number
  mfa_enabled: boolean
}

let accessToken: string | null = null
let currentUser: SessionUser | null = null
const listeners = new Set<() => void>()

const notify = () => listeners.forEach(fn => fn())

export function onSessionChange(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function getUser(): SessionUser | null {
  return currentUser
}

export function getAccessToken(): string | null {
  return accessToken
}

export function setSession(tokens: { access_token: string; user?: SessionUser }) {
  accessToken = tokens.access_token
  if (tokens.user) currentUser = tokens.user
  notify()
}

export function clearSession() {
  const wasSignedIn = accessToken !== null || currentUser !== null
  accessToken = null
  currentUser = null
  notify()
  if (wasSignedIn) {
    // The server revokes the refresh cookie's session and expires the cookie. A failure here does not matter;
    // the local session is already gone.
    originalFetch('/api/v1/auth/logout', { method: 'POST' }).catch(() => undefined)
  }
}

let refreshing: Promise<boolean> | null = null

// Asks the server to renew the session from the HttpOnly refresh cookie. Returns false when there is no valid cookie.
export async function renewSession(): Promise<boolean> {
  if (refreshing) return refreshing
  refreshing = (async () => {
    try {
      const res = await originalFetch('/api/v1/auth/refresh', { method: 'POST' })
      if (!res.ok) {
        accessToken = null
        currentUser = null
        notify()
        return false
      }
      const data = await res.json()
      accessToken = data.access_token
      currentUser = data.user ?? currentUser
      notify()
      return true
    } catch {
      return false
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

const PUBLIC_AUTH_PATHS = ['/api/v1/auth/login', '/api/v1/auth/setup', '/api/v1/auth/status', '/api/v1/auth/refresh', '/api/v1/auth/logout']
const originalFetch = window.fetch.bind(window)

function isApi(url: string): boolean {
  return url.startsWith('/api/v1') || url.includes('/api/v1/')
}

function isPublic(url: string): boolean {
  return PUBLIC_AUTH_PATHS.some(p => url.startsWith(p) || url.includes(p))
}

export function installFetchInterceptor() {
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    if (!isApi(url) || isPublic(url)) return originalFetch(input, init)

    const withAuth = (token: string | null): RequestInit => {
      const headers = new Headers(init?.headers ?? (typeof input !== 'string' && !(input instanceof URL) ? input.headers : undefined))
      if (token) headers.set('Authorization', `Bearer ${token}`)
      return { ...init, headers }
    }

    let response = await originalFetch(input, withAuth(accessToken))
    if (response.status === 401 && (await renewSession())) {
      response = await originalFetch(input, withAuth(accessToken))
    }
    if (response.status === 401) {
      clearSession()
    }
    return response
  }
}
