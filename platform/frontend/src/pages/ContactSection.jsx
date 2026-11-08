import { useState, useEffect } from 'react'
import '../styles/copy-feedback.css'
import { ArrowUpRight, Copy, Check, Mail, MessageSquareText } from 'lucide-react'

// L'adresse publique de support se configure lors du déploiement du frontend.
// Il n'y a pas de faux POST ni de simulation d'envoi au backend.
const configuredEmail = (import.meta.env.VITE_AUTOKOM_CONTACT_EMAIL || '').trim()
const canOpenEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(configuredEmail)

export default function ContactSection() {
  const [form, setForm] = useState({ name: '', email: '', subject: 'Question générale', message: '' })
  const [feedback, setFeedback] = useState('')
  const [pending, setPending] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => { if (!copied) return; const timer = window.setTimeout(() => setCopied(false), 3000); return () => window.clearTimeout(timer) }, [copied])

  function update(field, value) {
    setForm(previous => ({ ...previous, [field]: value }))
    setFeedback('')
  }

  async function handleSubmit(event) {
    event.preventDefault()
    if (!event.currentTarget.reportValidity()) return

    const subject = `[AUTOKOM] ${form.subject}`
    const body = `Nom : ${form.name.trim()}\nE-mail : ${form.email.trim()}\n\n${form.message.trim()}`
    if (canOpenEmail) {
      window.location.href = `mailto:${configuredEmail}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
      setFeedback('Votre logiciel de messagerie va s’ouvrir avec le message prérempli. Vérifiez puis confirmez son envoi.')
      return
    }

    setPending(true)
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard_unavailable')
      await navigator.clipboard.writeText(`${subject}\n\n${body}`)
      setCopied(true)
      setFeedback('Votre demande a été copiée. Aucun service d’envoi n’est encore configuré sur cette plateforme.')
    } catch {
      setFeedback('La copie automatique est indisponible. Sélectionnez votre texte pour le conserver. Aucun message n’a été envoyé.')
    } finally {
      setPending(false)
    }
  }

  return (
    <section className="lp-contact" id="contact" aria-labelledby="lp-contact-heading">
      <div className="lp-container lp-contact-layout">
        <div className="lp-contact-copy" data-lp-reveal>
          <span className="lp-kicker"><span className="lp-contact-kicker-mark" /> UNE QUESTION ?</span>
          <h2 id="lp-contact-heading">Parlons de<br /><em>votre projet.</em></h2>
          <p>Une question sur AUTOKOM, les déploiements ou les possibilités d’intégration ? Préparez votre message depuis cet espace.</p>
          <div className="lp-contact-benefit"><MessageSquareText size={18} aria-hidden="true" /> Un contact simple, sans compte obligatoire.</div>
          <span className="lp-contact-aside-note">ÉCHANGE & ACCOMPAGNEMENT</span>
        </div>
        <div className="lp-contact-frame" data-lp-reveal>
          <div className="lp-contact-panel">
            <div className="lp-contact-panel-top"><span><Mail size={15} aria-hidden="true" /> NOUS CONTACTER</span><span>ÉCRIVEZ-NOUS</span></div>
            <form className="lp-contact-form" onSubmit={handleSubmit}>
              <div className="lp-contact-field-grid">
                <label className="lp-contact-field"><span>Votre nom</span><input name="contact-name" autoComplete="name" placeholder="Nom et prénom" value={form.name} onChange={e => update('name', e.target.value)} maxLength={100} required /></label>
                <label className="lp-contact-field"><span>Adresse e-mail</span><input name="contact-email" type="email" autoComplete="email" placeholder="vous@exemple.com" value={form.email} onChange={e => update('email', e.target.value)} maxLength={254} required /></label>
              </div>
              <label className="lp-contact-field"><span>Sujet</span><select name="contact-subject" value={form.subject} onChange={e => update('subject', e.target.value)}><option>Question générale</option><option>Déploiement et intégration</option><option>Collaboration</option><option>Problème technique</option></select></label>
              <label className="lp-contact-field"><span>Votre message</span><textarea name="contact-message" rows={4} placeholder="Décrivez brièvement votre demande…" minLength={10} maxLength={4000} value={form.message} onChange={e => update('message', e.target.value)} required /></label>
              <button className={`lp-contact-submit ak-copy-action ${copied ? 'ak-copy-success' : ''}`} disabled={pending} type="submit">{canOpenEmail ? <Mail size={17} /> : copied ? <Check size={17} /> : <Copy size={17} />}{canOpenEmail ? 'Préparer un e-mail' : copied ? 'Copié !' : 'Copier ma demande'}<ArrowUpRight size={18} /></button>
              {feedback ? <p role="status" className="lp-contact-feedback">{feedback}</p> : <p className="lp-contact-privacy">{canOpenEmail ? 'Le message s’ouvre dans votre application e-mail. Aucun envoi automatique.' : 'L’envoi direct sera disponible après configuration du contact. Aucun message n’est transmis automatiquement.'}</p>}
            </form>
          </div>
        </div>
      </div>
    </section>
  )
}
