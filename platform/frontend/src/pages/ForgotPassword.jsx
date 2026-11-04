import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import AuthFrame from '../components/AuthFrame.jsx'
import { API_AVAILABLE } from '../config/runtime.js'
import { authApi } from '../services/authApi.js'
import '../styles/auth.css'

export default function ForgotPassword({ navigate, theme, onToggleTheme }) {
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  async function submit(event) {
    event.preventDefault()
    if (!API_AVAILABLE) { setNotice('Fonctionnalité disponible en mode API.'); return }
    setBusy(true)
    try { await authApi.forgotPassword(email.trim()); setNotice('Si ce compte existe, un e-mail de réinitialisation a été envoyé.') }
    catch (error) { setNotice(error?.message || 'Demande indisponible.') }
    finally { setBusy(false) }
  }
  return <AuthFrame kind="login" navigate={navigate} theme={theme} onToggleTheme={onToggleTheme}
    title="Mot de passe oublié." subtitle="Recevez un lien sécurisé pour retrouver votre compte.">
    <form className="auth-ui-form" onSubmit={submit}>
      <label className="auth-ui-field"><span>Adresse e-mail</span>
        <input type="email" required autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="vous@exemple.com"/>
      </label>
      {notice && <p className="auth-ui-notice" role="status">{notice}</p>}
      <button type="submit" className="auth-ui-primary" disabled={busy}>Envoyer le lien <ArrowRight size={19}/></button>
    </form>
    <p className="auth-ui-switch"><button type="button" onClick={() => navigate('/login')}>Retour à la connexion</button></p>
  </AuthFrame>
}
