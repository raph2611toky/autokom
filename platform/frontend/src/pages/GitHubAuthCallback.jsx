import { useEffect, useRef, useState } from 'react'
import { ArrowRight, CheckCircle2, GitBranch, LoaderCircle, LockKeyhole, ShieldCheck, XCircle } from 'lucide-react'
import AuthFrame from '../components/AuthFrame.jsx'
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

const STEPS = [
  { label: 'GitHub', detail: 'Autorisation de votre compte', icon: GitBranch },
  { label: 'Identité', detail: 'Vérification sécurisée', icon: ShieldCheck },
  { label: 'AUTOKOM', detail: 'Ouverture de votre espace', icon: LockKeyhole },
]

export default function GitHubAuthCallback({ navigate, theme, onToggleTheme }) {
  const { refreshUser } = useAuth()
  const started = useRef(false) // React StrictMode : code OAuth à usage unique.
  const [phase, setPhase] = useState(0)
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
      setPhase(1)
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
      setPhase(2)
      setNotice(result.status === 'created' ? 'Compte créé ! Préparation de votre espace…' : 'Connexion réussie ! Ouverture du tableau de bord…')
      await refreshUser() // La session réside exclusivement dans les cookies HttpOnly.
      setPhase(3)
      navigate('/dashboard')
    }
    finish().catch(error => {
      window.sessionStorage.removeItem(GITHUB_FINISH_SETUP_KEY)
      setNotice(error?.message || ERRORS.error)
      setFailed(true)
      setBusy(false)
    })
  }, [])

  return <AuthFrame kind="login" navigate={navigate} theme={theme} onToggleTheme={onToggleTheme}
    title="Connexion GitHub" subtitle="Votre espace AUTOKOM s’ouvre dans cet onglet, sans manipulation de jetons.">
    <div className="ak-github-callback" aria-live="polite" aria-atomic="true">
      <div className={`ak-github-orbit ${busy ? 'ak-github-orbit--active' : ''} ${failed ? 'ak-github-orbit--failed' : ''}`}>
        {failed ? <XCircle size={37}/> : phase === 3 || linked ? <CheckCircle2 size={37}/> : <GitBranch size={37}/>}
        {busy && <span className="ak-github-orbit-ring" aria-hidden="true"/>}
      </div>
      <div className="ak-github-steps" role="list">
        {STEPS.map(({ label, detail, icon: Icon }, index) => <div className={`ak-github-step ${index < phase ? 'is-done' : index === phase ? 'is-current' : ''}`} role="listitem" key={label}>
          <div className="ak-github-step-icon">{index < phase ? <CheckCircle2 size={20}/> : <Icon size={20}/>}</div>
          <div><strong>{label}</strong><small>{detail}</small></div>
          {index === phase && busy && <LoaderCircle size={15} className="ak-spin"/>}
        </div>)}
      </div>
      <p className={`ak-github-message ${failed ? 'is-error' : ''}`} role="status">{notice}</p>
      {!busy && <button type="button" className="auth-ui-primary" onClick={() => navigate(linked ? '/dashboard' : '/login')}>
        {linked ? 'Ouvrir mon espace' : 'Retour à la connexion'} <ArrowRight size={19}/>
      </button>}
      <small className="ak-github-security"><LockKeyhole size={13}/> Connexion chiffrée · Aucune clé GitHub stockée dans le navigateur</small>
    </div>
  </AuthFrame>
}
