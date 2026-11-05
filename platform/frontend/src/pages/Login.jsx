import { useState } from 'react'
import { ArrowRight, Eye, EyeOff, LockKeyhole } from 'lucide-react'
import AuthFrame from '../components/AuthFrame.jsx'
import GitHubMark from '../components/GitHubMark.jsx'
import { API_AVAILABLE, GITHUB_AVAILABLE } from '../config/runtime.js'
import { useAuth } from '../context/AuthContext.jsx'
import '../styles/auth.css'

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export default function Login({ navigate, theme, onToggleTheme }) {
  const { login } = useAuth()
  const [busy, setBusy] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [visible, setVisible] = useState(false)
  const [notice, setNotice] = useState(() => new URLSearchParams(window.location.search).get('verified') === '1' ? 'Votre adresse e-mail est vérifiée. Vous pouvez vous connecter.' : '')

  async function submit(event) {
    event.preventDefault()
    if (!emailPattern.test(email.trim()) || !password) {
      setNotice('Renseignez une adresse e-mail valide et votre mot de passe.')
      return
    }
    if (!API_AVAILABLE) { setPassword(''); navigate('/deploy'); return }
    setBusy(true)
    setNotice('')
    try {
      await login({ email: email.trim(), password })
      navigate('/deploy')
    } catch (error) {
      setNotice(error?.message || 'Connexion impossible.')
    } finally { setPassword(''); setBusy(false) }
  }

  return (
    <AuthFrame kind="login" navigate={navigate} theme={theme} onToggleTheme={onToggleTheme}
      title="Bon retour." subtitle={API_AVAILABLE ? 'Connectez-vous à votre compte AUTOKOM.' : 'Parcours de présentation : aucun compte réel n’est nécessaire.'}>
      <form className="auth-ui-form" onSubmit={submit} noValidate>
        <label className="auth-ui-field">
          <span>Adresse e-mail</span>
          <input type="email" name="email" autoComplete="username" required value={email}
            onChange={event => { setEmail(event.target.value); setNotice('') }} placeholder="vous@exemple.com" />
        </label>
        <div className="auth-ui-field">
          <label htmlFor="login-password">Mot de passe</label>
          <div className="auth-ui-password">
            <input id="login-password" type={visible ? 'text' : 'password'} autoComplete="current-password" required
              value={password} onChange={event => { setPassword(event.target.value); setNotice('') }} placeholder="Votre mot de passe" />
            <button type="button" onClick={() => setVisible(v => !v)} aria-label={visible ? 'Masquer le mot de passe' : 'Afficher le mot de passe'}>
              {visible ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>
          {API_AVAILABLE && (
            <button type="button" className="auth-ui-forgot" onClick={() => navigate('/forgot-password')}>
              Mot de passe oublié ?
            </button>
          )}
        </div>
        {notice && <p className="auth-ui-notice" role="status">{notice}</p>}
        <button type="submit" className="auth-ui-primary" disabled={busy} ><LockKeyhole size={18}/> Se connecter <ArrowRight size={19} /></button>
      </form>
      <p className="auth-ui-switch">Vous n'avez pas de compte ? <button type="button" onClick={() => navigate('/register')}>Créer un compte</button></p>
      <button type="button" className="auth-ui-explore auth-ui-github" onClick={() => {
        if (API_AVAILABLE) setNotice(GITHUB_AVAILABLE ? 'Connexion GitHub en cours d’intégration.' : 'Connexion GitHub non activée.')
        else navigate('/deploy')
      }}><GitHubMark size={17} /> Se connecter avec GitHub</button>
    </AuthFrame>
  )
}
