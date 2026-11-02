import { useState } from 'react'
import { ArrowRight, Eye, EyeOff, LockKeyhole } from 'lucide-react'

export default function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [visible, setVisible] = useState(false)
  const [notice, setNotice] = useState('')

  function submit(event) {
    event.preventDefault()
    if (!username.trim() || !password) {
      setNotice('Renseignez les deux champs.')
      return
    }
    // Aucun endpoint, aucune vérification factice, aucun stockage d'identifiants.
    setPassword('')
    setNotice('Connexion non disponible pour le moment.')
  }

  return (
    <main className="login-minimal" id="main-content">
      <section className="login-minimal-card" aria-labelledby="login-heading">
        <div className="login-minimal-body">
          <img className="login-minimal-logo" src="/autokom-logo.png" alt="" />
          <h1 id="login-heading">AUTOKOM</h1>
          <span className="login-minimal-title">Connexion</span>

          <form onSubmit={submit} className="login-minimal-form" noValidate>
            <label className="field">
              <span>Identifiant</span>
              <input autoComplete="username" name="username" value={username} onChange={(e) => { setUsername(e.target.value); setNotice('') }} placeholder="Identifiant administrateur" required />
            </label>
            <label className="field">
              <span>Mot de passe</span>
              <span className="password-field">
                <input autoComplete="current-password" name="password" type={visible ? 'text' : 'password'} value={password} onChange={(e) => { setPassword(e.target.value); setNotice('') }} placeholder="Mot de passe" required />
                <button type="button" className="show-password" onClick={() => setVisible((value) => !value)} aria-label={visible ? 'Masquer le mot de passe' : 'Afficher le mot de passe'}>
                  {visible ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </span>
            </label>
            {notice && <p className="login-minimal-notice" role="status">{notice}</p>}
            <button className="button-primary login-minimal-submit" type="submit">
              <LockKeyhole size={16} /> Se connecter <ArrowRight size={17} />
            </button>
          </form>
        </div>
      </section>
    </main>
  )
}
