import { useMemo, useState } from 'react'
import { ArrowRight, Check, Eye, EyeOff } from 'lucide-react'
import AuthFrame from '../components/AuthFrame.jsx'
import GitHubMark from '../components/GitHubMark.jsx'
import { API_AVAILABLE, GITHUB_AVAILABLE } from '../config/runtime.js'
import { authApi } from '../services/authApi.js'
import { startGitHubAccountAuth } from '../services/githubAuthFlow.js'
import '../styles/auth.css'

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const empty = { name: '', email: '', password: '', confirm: '', consent: false }

export default function Register({ navigate, theme, onToggleTheme }) {
  const [form, setForm] = useState(empty)
  const [visible, setVisible] = useState(false)
  const [touched, setTouched] = useState({})
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [serverErrors, setServerErrors] = useState({})
  const [registered, setRegistered] = useState(false)
  const checks = useMemo(() => [
    { label: '12 caractères', ok: form.password.length >= 12 },
    { label: 'Majuscule et minuscule', ok: /[A-Z]/.test(form.password) && /[a-z]/.test(form.password) },
    { label: 'Chiffre et symbole', ok: /\d/.test(form.password) && /[^a-zA-Z0-9]/.test(form.password) },
  ], [form.password])
  const strength = checks.filter(check => check.ok).length
  const errors = {
    name: form.name.trim().length < 2 ? 'Indiquez au moins 2 caractères.' : '',
    email: !emailPattern.test(form.email.trim()) ? 'Adresse e-mail invalide.' : '',
    password: strength < 3 ? 'Respectez les trois critères.' : '',
    confirm: !form.confirm || form.confirm !== form.password ? 'Les mots de passe ne correspondent pas.' : '',
    consent: !form.consent ? 'Acceptez les conditions pour continuer.' : '',
  }

  function update(key, value) {
    setForm(current => ({ ...current, [key]: value }))
    setNotice('')
    setServerErrors(current => ({ ...current, [key]: '' }))
  }
  function blur(key) { setTouched(current => ({ ...current, [key]: true })) }
  async function registerWithGithub() {
    if (!API_AVAILABLE) { navigate('/deploy'); return }
    if (!GITHUB_AVAILABLE) { setNotice('Inscription GitHub non activée.'); return }
    if (busy) return
    setBusy(true); setNotice('')
    try { await startGitHubAccountAuth('signup') }
    catch (error) { setNotice(error?.message || 'Inscription GitHub indisponible.'); setBusy(false) }
  }

  async function submit(event) {
    event.preventDefault()
    if (busy || registered) return
    setTouched({ name: true, email: true, password: true, confirm: true, consent: true })
    if (Object.values(errors).some(Boolean)) {
      setNotice('Vérifiez les champs signalés avant de continuer.')
      return
    }
    if (!API_AVAILABLE) {
      setForm(current => ({ ...current, password: '', confirm: '' }))
      navigate('/deploy')
      return
    }
    setBusy(true)
    try {
      const created = await authApi.register({ fullName: form.name.trim(), email: form.email.trim(), password: form.password, passwordConfirm: form.confirm })
      setRegistered(true)
      setForm(current => ({ ...current, password: '', confirm: '' }))
      setTouched({})
      setServerErrors({})
      // Une adresse n'est pas un secret ; permet de proposer « renvoyer » après redirection.
      try { sessionStorage.setItem('autokom.pendingVerificationEmail', form.email.trim()); sessionStorage.setItem('autokom.pendingVerificationMailSent', created?.verification_email_sent === false ? '0' : '1') } catch { /* stockage facultatif */ }
      navigate('/verify-email')
    } catch (error) {
      const fields = error?.errors || {}
      setServerErrors({
        name: fields.full_name?.[0] || '', email: fields.email?.[0] || '',
        password: fields.password?.[0] || '', confirm: fields.password_confirm?.[0] || '',
      })
      setNotice(error?.message === 'Données invalides' || error?.message === 'Données invalides.'
        ? 'Vérifiez les informations indiquées par le serveur.' : (error?.message || 'Inscription impossible.'))
    } finally { setBusy(false) }
  }

  return (
    <AuthFrame kind="register" navigate={navigate} theme={theme} onToggleTheme={onToggleTheme}
      title="Créer un compte." subtitle={API_AVAILABLE ? 'Créez votre compte et confirmez votre adresse e-mail.' : 'Testez le formulaire et continuez vers les projets, sans création de compte réel.'}>
      <form className="auth-ui-form auth-ui-register-form" onSubmit={submit} noValidate>
        <label className="auth-ui-field">
          <span>Nom complet</span>
          <input name="name" autoComplete="name" required value={form.name} placeholder="Nom et prénom"
            onChange={event => update('name', event.target.value)} onBlur={() => blur('name')}
            aria-invalid={!!(serverErrors.name || (touched.name && errors.name))} aria-describedby={(serverErrors.name || (touched.name && errors.name)) ? 'reg-name-error' : undefined} />
          {(serverErrors.name || (touched.name && errors.name)) && <small className="auth-ui-error" id="reg-name-error">{serverErrors.name || errors.name}</small>}
        </label>
        <label className="auth-ui-field">
          <span>Adresse e-mail</span>
          <input type="email" name="email" autoComplete="email" required value={form.email} placeholder="vous@exemple.com"
            onChange={event => update('email', event.target.value)} onBlur={() => blur('email')}
            aria-invalid={!!(serverErrors.email || (touched.email && errors.email))} aria-describedby={(serverErrors.email || (touched.email && errors.email)) ? 'reg-email-error' : undefined} />
          {(serverErrors.email || (touched.email && errors.email)) && <small className="auth-ui-error" id="reg-email-error">{serverErrors.email || errors.email}</small>}
        </label>
        <div className="auth-ui-field">
          <label htmlFor="register-password">Mot de passe</label>
          <div className="auth-ui-password">
            <input id="register-password" name="password" type={visible ? 'text' : 'password'} autoComplete="new-password" required
              value={form.password} onChange={event => update('password', event.target.value)} onBlur={() => blur('password')}
              aria-invalid={!!(serverErrors.password || (touched.password && errors.password))} aria-describedby="auth-ui-password-rules" placeholder="12 caractères minimum" />
            <button type="button" onClick={() => setVisible(value => !value)} aria-label={visible ? 'Masquer les mots de passe' : 'Afficher les mots de passe'}>
              {visible ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>
          {(serverErrors.password || (touched.password && errors.password)) && <small className="auth-ui-error">{serverErrors.password || errors.password}</small>}
        </div>
        <label className="auth-ui-field">
          <span>Confirmer le mot de passe</span>
          <input name="confirm" type={visible ? 'text' : 'password'} autoComplete="new-password" required
            value={form.confirm} onChange={event => update('confirm', event.target.value)} onBlur={() => blur('confirm')}
            aria-invalid={!!(serverErrors.confirm || (touched.confirm && errors.confirm))} aria-describedby={(serverErrors.confirm || (touched.confirm && errors.confirm)) ? 'reg-confirm-error' : undefined}
            placeholder="Répétez le mot de passe" />
          {(serverErrors.confirm || (touched.confirm && errors.confirm)) && <small className="auth-ui-error" id="reg-confirm-error">{serverErrors.confirm || errors.confirm}</small>}
        </label>
        <div className="auth-ui-strength auth-ui-full" id="auth-ui-password-rules" aria-label="Critères du mot de passe">
          <div className="auth-ui-strength-bars" role="meter" aria-label="Robustesse du mot de passe" aria-valuemin={0} aria-valuemax={3} aria-valuenow={strength}>
            {[1, 2, 3].map(n => <i key={n} className={strength >= n ? 'is-active' : ''} />)}
          </div>
          <div className="auth-ui-strength-rules">
            {checks.map(check => <span key={check.label} className={check.ok ? 'is-active' : ''}><Check size={13}/>{check.label}</span>)}
          </div>
        </div>
        <label className="auth-ui-consent auth-ui-full">
          <input type="checkbox" name="consent" checked={form.consent} onChange={event => update('consent', event.target.checked)} />
          <span>J’accepte les conditions d’utilisation.</span>
        </label>
        {touched.consent && errors.consent && <p className="auth-ui-error auth-ui-full">{errors.consent}</p>}
        {notice && <p className="auth-ui-notice auth-ui-full" role="status">{notice}</p>}
        <button type="submit" className="auth-ui-primary auth-ui-full" disabled={busy || registered}>{busy ? 'Création du compte…' : 'Créer mon compte'} <ArrowRight size={19}/></button>
      </form>
      <p className="auth-ui-switch">Vous avez déjà un compte ? <button type="button" onClick={() => navigate('/login')}>Se connecter</button></p>
      <button type="button" className="auth-ui-explore auth-ui-github" onClick={registerWithGithub} disabled={busy}><GitHubMark size={17} /> S’inscrire avec GitHub</button>
    </AuthFrame>
  )
}
