// Platform sign-in gate. Shown whenever /api/platform/apps returns 401 (no session).
// On success the parent re-fetches the per-user app list and renders the shell.
import { useState } from 'react'
import { Button, platformApi } from '@web-core'

export default function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      await platformApi.login(username, password)
      onLoggedIn()
    } catch (ex) {
      setErr((ex as Error).message || 'Sign in failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={submit}>
        <h2>Platform</h2>
        <p className="sub">Sign in to continue.</p>
        <label htmlFor="u">Username</label>
        <input id="u" type="text" autoFocus autoComplete="username"
          value={username} onChange={(e) => setUsername(e.target.value)} />
        <label htmlFor="p">Password</label>
        <input id="p" type="password" autoComplete="current-password"
          value={password} onChange={(e) => setPassword(e.target.value)} />
        {err && <p className="error-line">{err}</p>}
        <Button type="submit" disabled={busy || !username || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </div>
  )
}
