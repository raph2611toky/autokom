import { useState } from 'react'
import { ArrowRight, Eye, EyeOff, LockKeyhole } from 'lucide-react'
import AuthFrame from '../components/AuthFrame.jsx'
import '../styles/auth.css'

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export default function Login({ navigate, theme, onToggleTheme }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [visible, setVisible] = useState(false)
  const [notice, setNotice] = useState(() => new URLSearchParams(window.location.search).get('verified') === '1' ? 'Votre adresse e-mail est vérifiée. Vous pouvez vous connecter.' : '')

  function submit(event) {
    event.preventDefault()
    if (!emailPattern.test(email.trim()) || !password) {
      setNotice('Renseignez une adresse e-mail valide et votre mot de passe.')
      return
    }
    setPassword('')
    navigate('/deploy')
  }

  return (
    <AuthFrame kind="login" navigate={navigate} theme={theme} onToggleTheme={onToggleTheme}
      title="Bon retour." subtitle="Parcours de présentation : aucun compte réel n’est nécessaire.">
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
        </div>
        {notice && <p className="auth-ui-notice" role="status">{notice}</p>}
        <button type="submit" className="auth-ui-primary" ><LockKeyhole size={18}/> Se connecter <ArrowRight size={19} /></button>
      </form>
      <button type="button" className="auth-ui-explore" onClick={() => navigate('/deploy')}>Continuer avec GitHub</button>
      <p className="auth-ui-switch">Vous n'avez pas de compte ? <button type="button" onClick={() => navigate('/register')}>Créer un compte</button></p>
      <button type="button" className="auth-ui-explore" onClick={() => navigate('/deploy')}>Configurer un projet <ArrowRight size={15}/></button>
    </AuthFrame>
  )
}
