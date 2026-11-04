import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import AuthFrame from '../components/AuthFrame.jsx'
import { API_AVAILABLE } from '../config/runtime.js'
import { authApi } from '../services/authApi.js'
import '../styles/auth.css'

// Le token n'est pas placé dans localStorage, cookie ou attribut HTML.
let cachedResetToken
const tokenInUrl = () => {
  if (cachedResetToken !== undefined) return cachedResetToken
  const url = new URL(window.location.href)
  cachedResetToken = url.pathname === '/reset-password' ? (url.searchParams.get('token') || '') : ''
  if (url.pathname === '/reset-password') {
    url.searchParams.delete('token')
    window.history.replaceState(null, '', url.pathname + url.search + url.hash)
  }
  return cachedResetToken
}
export default function ResetPassword({ navigate, theme, onToggleTheme }) {
  const [token] = useState(tokenInUrl)
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  async function submit(event) {
    event.preventDefault()
    if (!API_AVAILABLE || !token) { setNotice('Lien manquant ou fonctionnalité indisponible.'); return }
    if (password.length < 12 || confirm !== password) { setNotice('Vérifiez votre mot de passe (12 caractères minimum) et sa confirmation.'); return }
    setBusy(true)
    try { await authApi.resetPassword({ token, newPassword: password, confirm }); setNotice('Mot de passe modifié. Reconnectez-vous.') }
    catch (error) { setNotice(error?.message || 'Lien expiré ou invalide.') }
    finally { setPassword(''); setConfirm(''); setBusy(false) }
  }
  return <AuthFrame kind="login" navigate={navigate} theme={theme} onToggleTheme={onToggleTheme}
    title="Nouveau mot de passe." subtitle="Choisissez un mot de passe sécurisé pour votre compte.">
    <form className="auth-ui-form" onSubmit={submit}>
      <label className="auth-ui-field"><span>Nouveau mot de passe</span>
        <input type="password" autoComplete="new-password" required minLength={12} value={password} onChange={e => setPassword(e.target.value)}/></label>
      <label className="auth-ui-field"><span>Confirmer le mot de passe</span>
        <input type="password" autoComplete="new-password" required value={confirm} onChange={e => setConfirm(e.target.value)}/></label>
      {notice && <p className="auth-ui-notice" role="status">{notice}</p>}
      <button className="auth-ui-primary" type="submit" disabled={busy}>Mettre à jour <ArrowRight size={19}/></button>
    </form>
    <p className="auth-ui-switch"><button type="button" onClick={() => navigate('/login')}>Retour à la connexion</button></p>
  </AuthFrame>
}
