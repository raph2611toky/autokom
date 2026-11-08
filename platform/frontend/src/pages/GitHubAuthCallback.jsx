import { useEffect, useRef, useState } from 'react'
import { ArrowRight, CheckCircle2, LoaderCircle, XCircle } from 'lucide-react'
import GitHubMark from '../components/GitHubMark.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { authApi } from '../services/authApi.js'
import { consumeGitHubAccountCallback, GITHUB_FINISH_SETUP_KEY } from '../services/githubAuthFlow.js'
import '../styles/githubCallback.css'

const ERRORS = Object.freeze({
  denied: 'Autorisation GitHub annulée. Vous pouvez réessayer.',
  error: 'GitHub n’a pas pu valider la connexion. Réessayez.',
  invalid: 'Session expirée ou navigateur différent. Recommencez depuis AUTOKOM.',
  account_exists: 'Cette adresse e-mail est déjà utilisée dans AUTOKOM. Connectez-vous avec votre mot de passe, puis associez GitHub.',
  already_linked: 'Ce compte GitHub est déjà associé à un autre utilisateur.',
  not_registered: 'Ce compte n’est pas encore inscrit. Choisissez « S’inscrire avec GitHub ».',
})


export default function GitHubAuthCallback({ navigate }) {
  const { refreshUser } = useAuth()
  const started = useRef(false) // React StrictMode : code OAuth à usage unique.
  const [notice, setNotice] = useState('Connexion sécurisée avec GitHub…')
  const [busy, setBusy] = useState(true)
  const [linked, setLinked] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    const callback = consumeGitHubAccountCallback(
      window.location.search, window.location.hash, window.sessionStorage,
    )
    // Supprime code/state et éventuel ancien ticket AVANT tout appel réseau.
    window.history.replaceState(window.history.state, '', '/auth/github/callback')
    const finish = async () => {
      if (callback.status === 'linked') {
        setLinked(true)
        setNotice('Votre compte GitHub est associé à AUTOKOM.')
        setBusy(false)
        return
      }
      if (!['pending', 'ok', 'created'].includes(callback.status)) {
        throw new Error(ERRORS[callback.status] || ERRORS.invalid)
      }
      setNotice('Vérification de votre identité avec GitHub…')
      const result = callback.status === 'pending'
        ? await authApi.githubComplete(callback)
        : { ...(await authApi.githubExchange(callback)), status: callback.status }
      if (result.status === 'linked') {
        setLinked(true)
        setNotice('Compte GitHub associé. Connexion disponible.')
        setBusy(false)
        return
      }
      if (!['ok', 'created'].includes(result.status)) {
        throw new Error(ERRORS[result.status] || ERRORS.error)
      }
      setNotice(result.status === 'created' ? 'Compte créé ! Préparation de votre espace…' : 'Connexion réussie ! Ouverture du tableau de bord…')
      await refreshUser() // La session réside exclusivement dans les cookies HttpOnly.
      navigate('/dashboard')
    }
    finish().catch(error => {
      window.sessionStorage.removeItem(GITHUB_FINISH_SETUP_KEY)
      setNotice(error?.message || ERRORS.error)
      setFailed(true)
      setBusy(false)
    })
  }, [])

  return (
    <main className="auth-ui ak-github-callback-page" id="main-content">
      <section className="ak-github-callback" aria-labelledby="ak-github-callback-title">
        <h1 id="ak-github-callback-title" className="ak-github-callback-title">Connexion GitHub</h1>
        <div className="ak-github-transfer" aria-label="Transfert sécurisé de GitHub vers AUTOKOM">
          <span className="ak-github-transfer-mark ak-github-transfer-mark--github"><GitHubMark size={39}/></span>
          <span className={`ak-github-transfer-middle ${failed ? 'is-error' : ''}`} aria-hidden="true">
            {failed ? <XCircle size={25}/> : !busy ? <CheckCircle2 size={25}/> : <LoaderCircle size={25} className="ak-github-transfer-spinner"/>}
          </span>
          <span className="ak-github-transfer-mark ak-github-transfer-mark--autokom">
            <img src="/autokom-logo.png" alt="AUTOKOM" width="47" height="47" />
          </span>
        </div>
        <p className={`ak-github-message ${failed ? 'is-error' : ''}`} role="status" aria-live="polite">{notice}</p>
        {!busy && <button type="button" className="auth-ui-primary ak-github-callback-action" onClick={() => navigate(linked ? '/dashboard' : '/login')}>
          {linked ? 'Ouvrir mon espace' : 'Retour à la connexion'} <ArrowRight size={17}/>
        </button>}
      </section>
    </main>
  )
}
