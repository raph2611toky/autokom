import { useEffect, useRef, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import AuthFrame from '../components/AuthFrame.jsx'
import { API_AVAILABLE } from '../config/runtime.js'
import { authApi } from '../services/authApi.js'
import '../styles/auth.css'

/** Même AuthFrame / styles / boutons que Login et Register, aucune nouvelle DA. */
export default function VerifyEmail({ navigate, theme, onToggleTheme }) {
  const [message, setMessage] = useState('Vérification du lien…')
  const started = useRef(false)
  useEffect(() => {
    if (started.current) return
    started.current = true
    const url = new URL(window.location.href)
    const token = url.searchParams.get('token') || ''
    url.searchParams.delete('token')
    window.history.replaceState(null, '', url.pathname + url.search + url.hash)
    if (!API_AVAILABLE) { setMessage('Mode démonstration : vérification e-mail indisponible.'); return }
    if (!token) { setMessage('Lien de vérification manquant ou invalide.'); return }
    authApi.verifyEmail(token)
      .then(() => setMessage('Votre adresse e-mail est vérifiée. Vous pouvez vous connecter.'))
      .catch(error => setMessage(error?.message || 'Le lien est invalide ou expiré.'))
  }, [])
  return <AuthFrame kind="login" navigate={navigate} theme={theme} onToggleTheme={onToggleTheme}
    title="Confirmer votre adresse." subtitle="Sécurisation de votre compte AUTOKOM.">
    <p className="auth-ui-notice" role="status">{message}</p>
    <button type="button" className="auth-ui-primary" onClick={() => navigate('/login')}>Se connecter <ArrowRight size={19}/></button>
  </AuthFrame>
}
