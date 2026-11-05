import { useEffect, useRef, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import AuthFrame from '../components/AuthFrame.jsx'
import { API_AVAILABLE } from '../config/runtime.js'
import { authApi } from '../services/authApi.js'
import '../styles/auth.css'

/** Même AuthFrame, mêmes classes et couleurs que l'inscription existante. */
export default function VerifyEmail({ navigate, theme, onToggleTheme }) {
  const [status, setStatus] = useState('loading')
  const [message, setMessage] = useState('Vérification du lien…')
  const [resending, setResending] = useState(false)
  const started = useRef(false)
  const [email] = useState(() => {
    try { return sessionStorage.getItem('autokom.pendingVerificationEmail') || '' } catch { return '' }
  })

  useEffect(() => {
    if (started.current) return
    started.current = true
    const url = new URL(window.location.href)
    const token = url.searchParams.get('token') || ''
    url.searchParams.delete('token')
    window.history.replaceState(null, '', url.pathname + url.search + url.hash)
    if (!API_AVAILABLE) { setStatus('demo'); setMessage('Mode démonstration : vérification e-mail indisponible.'); return }
    if (!token) {
      let sent = true
      try { sent = sessionStorage.getItem('autokom.pendingVerificationMailSent') !== '0' } catch { /* facultatif */ }
      setStatus('pending')
      setMessage(email && !sent ? 'Le compte est créé, mais l’envoi de l’e-mail a échoué. Utilisez « Renvoyer ».' : email ? `Un message de confirmation a été envoyé à ${email}. Consultez votre boîte e-mail et cliquez sur le lien reçu.` :
        'Ouvrez le lien de vérification envoyé à votre adresse e-mail.')
      return
    }
    authApi.verifyEmail(token)
      .then(() => {
        setStatus('verified')
        setMessage('Votre adresse e-mail est vérifiée. Vous pouvez vous connecter.')
        try { sessionStorage.removeItem('autokom.pendingVerificationEmail') } catch { /* facultatif */ }
      })
      .catch(error => { setStatus('error'); setMessage(error?.message || 'Le lien est invalide ou expiré.') })
  }, [email])

  async function resend() {
    if (!email || resending) return
    setResending(true)
    try {
      await authApi.resendEmail(email)
      setMessage('Si cette adresse nécessite une confirmation, un nouveau message a été envoyé.')
      setStatus('pending')
    } catch (error) { setMessage(error?.message || 'Renvoi indisponible pour le moment.') }
    finally { setResending(false) }
  }

  return <AuthFrame kind="login" navigate={navigate} theme={theme} onToggleTheme={onToggleTheme}
    title="Confirmer votre adresse." subtitle="Sécurisation de votre compte AUTOKOM.">
    <p className="auth-ui-notice" role="status">{message}</p>
    {(status === 'pending' || status === 'error') && email && <button type="button" className="auth-ui-explore" disabled={resending} onClick={resend}>
      {resending ? 'Envoi en cours…' : 'Renvoyer le message de vérification'}
    </button>}
    <button type="button" className="auth-ui-primary" onClick={() => navigate('/login')}>Se connecter <ArrowRight size={19}/></button>
  </AuthFrame>
}
