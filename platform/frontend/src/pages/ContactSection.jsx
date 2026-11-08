import { useState } from 'react'
import { ArrowUpRight, CheckCircle2, LoaderCircle, Mail, MessageSquareText, AlertCircle } from 'lucide-react'
import { API_AVAILABLE } from '../config/runtime.js'
import { api } from '../services/api.js'

const EMPTY = { name: '', email: '', subject: 'Question générale', message: '', website: '' }

/** Formulaire public : transport même origine, CSRF et SMTP côté Django. */
export default function ContactSection() {
  const [form, setForm] = useState(EMPTY)
  const [feedback, setFeedback] = useState(null)
  const [pending, setPending] = useState(false)

  function update(field, value) {
    setForm(previous => ({ ...previous, [field]: value }))
    setFeedback(null)
  }

  async function handleSubmit(event) {
    event.preventDefault()
    if (pending || !event.currentTarget.reportValidity()) return
    if (!API_AVAILABLE) {
      setFeedback({ kind: 'error', text: 'Le formulaire de contact sera disponible lorsque le backend AUTOKOM sera connecté.' })
      return
    }
    setPending(true)
    setFeedback(null)
    try {
      await api.post('/contact/', {
        name: form.name.trim(), email: form.email.trim(), subject: form.subject,
        message: form.message.trim(), website: form.website,
      }, { retry: false })
      setForm(EMPTY)
      setFeedback({ kind: 'success', text: 'Votre message a été transmis au support AUTOKOM.' })
    } catch (error) {
      setFeedback({ kind: 'error', text: error?.status === 429
        ? 'Trop de demandes. Réessayez plus tard.'
        : error?.status === 400
          ? 'Vérifiez les informations du formulaire puis réessayez.'
          : 'Le message n’a pas pu être envoyé. Réessayez plus tard.' })
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
          <p>Une question sur AUTOKOM, les déploiements ou les possibilités d’intégration ? Envoyez directement votre demande à notre équipe de support.</p>
          <div className="lp-contact-benefit"><MessageSquareText size={18} aria-hidden="true" /> Un contact simple, sans compte obligatoire.</div>
          <span className="lp-contact-aside-note">ÉCHANGE & ACCOMPAGNEMENT</span>
        </div>
        <div className="lp-contact-frame" data-lp-reveal>
          <div className="lp-contact-panel">
            <div className="lp-contact-panel-top"><span><Mail size={15} aria-hidden="true" /> NOUS CONTACTER</span><span>ÉCRIVEZ-NOUS</span></div>
            <form className="lp-contact-form" onSubmit={handleSubmit} aria-busy={pending}>
              <div className="lp-contact-field-grid">
                <label className="lp-contact-field"><span>Votre nom</span><input name="contact-name" autoComplete="name" placeholder="Nom et prénom" value={form.name} onChange={e => update('name', e.target.value)} minLength={2} maxLength={100} disabled={pending} required /></label>
                <label className="lp-contact-field"><span>Adresse e-mail</span><input name="contact-email" type="email" autoComplete="email" placeholder="vous@exemple.com" value={form.email} onChange={e => update('email', e.target.value)} maxLength={254} disabled={pending} required /></label>
              </div>
              <label className="lp-contact-field"><span>Sujet</span><select name="contact-subject" value={form.subject} onChange={e => update('subject', e.target.value)} disabled={pending}><option>Question générale</option><option>Déploiement et intégration</option><option>Collaboration</option><option>Problème technique</option></select></label>
              <label className="lp-contact-field"><span>Votre message</span><textarea name="contact-message" rows={4} placeholder="Décrivez brièvement votre demande…" minLength={10} maxLength={4000} value={form.message} onChange={e => update('message', e.target.value)} disabled={pending} required /></label>
              <label className="lp-contact-honeypot" aria-hidden="true">Site web <input name="website" tabIndex={-1} autoComplete="off" value={form.website} onChange={e => update('website', e.target.value)} /></label>
              <button className="lp-contact-submit" disabled={pending} type="submit">{pending ? <LoaderCircle size={17} className="lp-contact-spinning" aria-hidden="true" /> : <Mail size={17} aria-hidden="true" />}{pending ? 'Envoi en cours…' : 'Envoyer mon message'}<ArrowUpRight size={18} aria-hidden="true" /></button>
              {feedback ? <p role={feedback.kind === 'error' ? 'alert' : 'status'} className={`lp-contact-feedback lp-contact-feedback-${feedback.kind}`}>{feedback.kind === 'success' ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}{feedback.text}</p> : <p className="lp-contact-privacy">Votre message est transmis au support AUTOKOM. Aucun compte n’est nécessaire.</p>}
            </form>
          </div>
        </div>
      </div>
    </section>
  )
}
