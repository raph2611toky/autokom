import { useEffect, useRef, useState } from 'react'
import { ArrowRight, LoaderCircle } from 'lucide-react'
import AuthFrame from '../components/AuthFrame.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { authApi } from '../services/authApi.js'
import { consumeGitHubAccountCallback, GITHUB_FINISH_SETUP_KEY } from '../services/githubAuthFlow.js'

const ERRORS = Object.freeze({
  denied: 'Vous avez annulé ou refusé l’autorisation GitHub.',
  error: 'La connexion GitHub a échoué côté serveur. Consultez les journaux AUTOKOM.',
  invalid: 'Session de connexion GitHub absente ou expirée. Réessayez depuis ce navigateur.',
  account_exists: 'Cette adresse e-mail possède déjà un compte AUTOKOM. Connectez-vous avec votre mot de passe avant d’associer GitHub à ce compte.',
  already_linked: 'Ce compte GitHub est déjà associé à un autre compte AUTOKOM.',
  not_registered: 'Aucun compte AUTOKOM associé à ce GitHub. Choisissez « S’inscrire avec GitHub » pour créer votre compte.',
})

export default function GitHubAuthCallback({ navigate, theme, onToggleTheme }) {
  const { refreshUser } = useAuth()
  const started = useRef(false) // StrictMode React : le ticket ne doit être utilisé qu'une fois.
  const [notice, setNotice] = useState('Vérification sécurisée de votre compte GitHub…')
  const [busy, setBusy] = useState(true)
  const [linked, setLinked] = useState(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    const callback = consumeGitHubAccountCallback(window.location.hash, window.sessionStorage)
    // Retirer immédiatement le ticket de l'URL (historique, captures et navigateur).
    window.history.replaceState(window.history.state, '', '/auth/github/callback')
    if (!['ok', 'created'].includes(callback.status)) window.sessionStorage.removeItem(GITHUB_FINISH_SETUP_KEY)
    if (callback.status === 'linked') {
      setNotice('Compte GitHub associé. Vous pouvez désormais vous connecter à AUTOKOM avec GitHub.')
      setLinked(true)
      setBusy(false)
      return
    }
    if (callback.status !== 'ok' && callback.status !== 'created') {
      setNotice(ERRORS[callback.status] || ERRORS.error)
      setBusy(false)
      return
    }
    authApi.githubExchange({ ticket: callback.ticket, browserBinding: callback.browserBinding })
      .then(() => refreshUser())
      .then(() => navigate('/deploy'))
      .catch(() => {
        setNotice('La session GitHub n’a pas pu être établie. Réessayez depuis la connexion.')
        setBusy(false)
      })
  }, [])

  return <AuthFrame kind="login" navigate={navigate} theme={theme} onToggleTheme={onToggleTheme}
    title="Connexion GitHub." subtitle="Vérification de votre identité et de la session AUTOKOM.">
    <div className="auth-ui-form" role="status" aria-live="polite">
      <p className="auth-ui-notice">{busy && <LoaderCircle size={17} className="ak-spin"/>} {notice}</p>
      {!busy && <button type="button" className="auth-ui-primary" onClick={() => navigate(linked ? '/deploy' : '/login')}>
        {linked ? 'Retour aux projets' : 'Revenir à la connexion'} <ArrowRight size={19}/>
      </button>}
    </div>
  </AuthFrame>
}
